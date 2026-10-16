import { useState } from "preact/hooks";
import * as api from "../api.js";
import * as ui from "../ui.jsx";
import styles from "./tabs.module.css";

const DEFAULT_PROMPT = "A cozy reading nook by a rain-streaked window at dusk, warm lamp light, a cat asleep on a stack of books, film photograph.";
const SIZES = ["512x512", "768x768", "1024x1024", "1328x1328", "1024x768", "768x1024", "1664x928", "928x1664"];

function requestBody(prompt, negative, size, steps, seed) {
  const body = { prompt: prompt.trim(), size, steps };
  if (negative.trim()) body.negative_prompt = negative.trim();
  if (seed !== "") body.seed = Number(seed);
  return body;
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

  const body = requestBody(prompt, negative, size, steps, seed);
  const curl = `${api.curlJson("/v1/images/generations", body)} \\\n  | jq -r '.data[0].b64_json' | base64 -d > omni.png`;

  const generate = async () => {
    if (!body.prompt) return;
    setBusy(true);
    setMeta("generating…");
    const start = performance.now();
    try {
      const response = await fetch("/v1/images/generations", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      });
      const elapsed = api.since(start);
      api.record("image", response.status, elapsed);
      if (!response.ok) throw new Error(await api.errorText(response));
      const result = await response.json();
      const seedText = body.seed != null ? ` · seed ${body.seed}` : "";
      const caption = `${body.size} · ${body.steps} steps${seedText} · ${(elapsed / 1000).toFixed(1)} s`;
      const created = result.data.map((image) => ({
        id: crypto.randomUUID(),
        prompt: body.prompt,
        url: `data:image/png;base64,${image.b64_json}`,
        caption,
      }));
      setItems((current) => [...created, ...current]);
      setMeta(`${elapsed} ms`);
    } catch (error) {
      setItems((current) => [{ id: crypto.randomUUID(), error: error.message }, ...current]);
      setMeta("");
    } finally {
      setBusy(false);
    }
  };

  return (
    <ui.Page title="Image" subtitle="Text to image with a local diffusion model.">
      <ui.Columns>
        <ui.Card title="Prompt" meta={api.models.value.defaults.image}>
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
                  {SIZES.map((option) => <option value={option}>{option}</option>)}
                </select>
              </ui.Field>
              <ui.Field label="Seed">
                <input type="number" placeholder="blank = random" value={seed} onInput={(event) => setSeed(event.currentTarget.value)} />
              </ui.Field>
            </ui.Grid>
            <ui.Field label="Steps" trailing={<ui.Meta>{steps}</ui.Meta>}>
              <input type="range" min={1} max={60} value={steps} onInput={(event) => setSteps(Number(event.currentTarget.value))} />
            </ui.Field>
            <ui.Notice tone="info">First request loads the model (~1 min); each image takes a while on Apple silicon. Fewer steps and smaller sizes are faster.</ui.Notice>
            <ui.Row end>
              <ui.Button variant="primary" disabled={busy} onClick={generate}>{busy ? "Generating…" : "Generate"}</ui.Button>
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
