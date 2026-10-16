import { useEffect, useRef, useState } from "preact/hooks";
import { record } from "../api/client.js";
import { generateImage } from "../api/endpoints.js";
import { models } from "../api/models.js";
import { curlJson } from "../lib/curl.js";
import { since } from "../lib/format.js";
import * as ui from "../components/ui.jsx";
import styles from "./tabs.module.css";

const DEFAULT_PROMPT = "A cozy reading nook by a rain-streaked window at dusk, warm lamp light, a cat asleep on a stack of books, film photograph.";
const SIZES = ["512x512", "768x768", "1024x1024", "1328x1328", "1024x768", "768x1024", "1664x928", "928x1664"];
const MAX_ITEMS = 20;

function requestBody(prompt, negative, size, steps, seed) {
  const body = { prompt: prompt.trim(), size, steps };
  if (negative.trim()) body.negative_prompt = negative.trim();
  // Server rejects floats: round to an integer, or omit when not numeric.
  const rounded = seed === "" ? NaN : Math.round(Number(seed));
  if (Number.isFinite(rounded)) body.seed = rounded;
  return body;
}

function base64ToObjectUrl(base64, type = "image/png") {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index++) bytes[index] = binary.charCodeAt(index);
  return URL.createObjectURL(new Blob([bytes], { type }));
}

function Figure({ item }) {
  if (item.error) return <li class={styles.figure}><ui.ErrorText>{item.error}</ui.ErrorText></li>;
  return (
    <li class={styles.figure}>
      <img class={styles.picture} src={item.url} alt={item.prompt} />
      <p class={styles.clipText}>{item.prompt}</p>
      <div class={styles.clipFoot}>
        <ui.Meta>{item.caption}</ui.Meta>
        <a class={styles.link} href={item.url} download="omni.png">download .png</a>
      </div>
    </li>
  );
}

export default function Image() {
  const [prompt, setPrompt] = useState(DEFAULT_PROMPT);
  const [negative, setNegative] = useState("");
  const [size, setSize] = useState("1024x1024");
  const [steps, setSteps] = useState(40);
  const [seed, setSeed] = useState("");
  const [busy, setBusy] = useState(false);
  const [items, setItems] = useState([]);
  const [meta, setMeta] = useState("");
  const itemsRef = useRef([]);
  const abort = useRef(null);

  useEffect(() => () => {
    abort.current?.abort();
    itemsRef.current.forEach((item) => item.url && URL.revokeObjectURL(item.url));
  }, []);

  // Cap retained images and revoke the blob URLs we drop.
  const commit = (next) => {
    const kept = next.slice(0, MAX_ITEMS);
    for (const dropped of next.slice(MAX_ITEMS)) {
      if (dropped.url) URL.revokeObjectURL(dropped.url);
    }
    itemsRef.current = kept;
    setItems(kept);
  };

  const body = requestBody(prompt, negative, size, steps, seed);
  const curl = `${curlJson("/v1/images/generations", body)} \\\n  | jq -r '.data[0].b64_json' | base64 -d > omni.png`;

  const generate = async () => {
    if (!body.prompt) return;
    const controller = new AbortController();
    abort.current = controller;
    setBusy(true);
    setMeta("generating…");
    const start = performance.now();
    try {
      const { status, data: result } = await generateImage(body, controller.signal);
      const elapsed = since(start);
      record("image", status, elapsed);
      const seedText = body.seed != null ? ` · seed ${body.seed}` : "";
      const caption = `${body.size} · ${body.steps} steps${seedText} · ${(elapsed / 1000).toFixed(1)} s`;
      const created = (result.data ?? []).map((image) => ({
        id: crypto.randomUUID(),
        prompt: body.prompt,
        url: base64ToObjectUrl(image.b64_json),
        caption,
      }));
      commit([...created, ...itemsRef.current]);
      setMeta(`${elapsed} ms`);
    } catch (error) {
      if (error.name === "AbortError") {
        setMeta("");
      } else {
        record("image", error.status ?? 0, since(start));
        commit([{ id: crypto.randomUUID(), error: error.message }, ...itemsRef.current]);
        setMeta("");
      }
    } finally {
      abort.current = null;
      setBusy(false);
    }
  };

  return (
    <ui.Page title="Image" subtitle="Text to image with a local diffusion model.">
      <ui.Columns>
        <ui.Card title="Prompt" meta={models.value.defaults.image}>
          <ui.Stack>
            <ui.Field label="Prompt">
              <textarea rows={4} value={prompt} onInput={(event) => setPrompt(event.currentTarget.value)} />
            </ui.Field>
            <ui.Field label="Negative prompt">
              <input placeholder="optional" value={negative} onInput={(event) => setNegative(event.currentTarget.value)} />
            </ui.Field>
            <ui.Grid>
              <ui.Field label="Size">
                <select value={size} onChange={(event) => setSize(event.currentTarget.value)}>
                  {SIZES.map((option) => <option key={option} value={option}>{option}</option>)}
                </select>
              </ui.Field>
              <ui.Field label="Seed">
                <input type="number" step={1} placeholder="blank = random" value={seed} onInput={(event) => setSeed(event.currentTarget.value)} />
              </ui.Field>
            </ui.Grid>
            <ui.Field label="Steps" trailing={<ui.Meta>{steps}</ui.Meta>}>
              <input type="range" min={1} max={60} value={steps} onInput={(event) => setSteps(Number(event.currentTarget.value))} />
            </ui.Field>
            <ui.Notice tone="info">First request loads the model (~1 min); each image takes a while on Apple silicon. Fewer steps and smaller sizes are faster.</ui.Notice>
            <ui.Row end>
              {busy
                ? <ui.Button variant="stop" onClick={() => abort.current?.abort()}>Stop</ui.Button>
                : <ui.Button variant="primary" onClick={generate}>Generate</ui.Button>}
            </ui.Row>
          </ui.Stack>
        </ui.Card>
        <ui.Card title="Images" meta={meta}>
          <ui.Stack>
            {items.length
              ? <ul class={styles.gallery}>{items.map((item) => <Figure key={item.id} item={item} />)}</ul>
              : <ui.Empty>Generated images appear here; each can be downloaded.</ui.Empty>}
            <ui.CurlBlock code={curl} />
          </ui.Stack>
        </ui.Card>
      </ui.Columns>
    </ui.Page>
  );
}
