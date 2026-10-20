import { useState } from "preact/hooks";
import { record } from "../api/client.js";
import { generateImage } from "../api/endpoints.js";
import { curlJson } from "../lib/curl.js";
import { since } from "../lib/format.js";
import * as ui from "../components/ui.jsx";
import { Panel, Pending, RequestBody, Turn, TurnError, UiJson, Workbench, useTurnState } from "../components/workbench.jsx";
import { LogsCard } from "../components/LogsCard.jsx";
import { useJsonRequest } from "../hooks/useJsonRequest.js";
import { useRunHistory } from "../hooks/useRunHistory.js";
import styles from "./Image.module.css";

const DEFAULT_PROMPT = "A cozy reading nook by a rain-streaked window at dusk, warm lamp light, a cat asleep on a stack of books, film photograph.";
const SIZES = ["512x512", "768x768", "1024x1024", "1328x1328", "1024x768", "768x1024", "1664x928", "928x1664"];
const STEPS_MIN = 1;
const STEPS_MAX = 60;
const SEED_MAX = 2 ** 31 - 1;

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

export default function Image() {
  const [prompt, setPrompt] = useState(DEFAULT_PROMPT);
  const [negative, setNegative] = useState("");
  const [size, setSize] = useState("512x512");
  const [steps, setSteps] = useState(8);
  const [seed, setSeed] = useState("");
  const [meta, setMeta] = useState("");
  const [view, setView] = useState("ui");
  const { entries: turns, busy, start: startRun, stop, finish, add, update, clear } = useRunHistory();
  const turnState = useTurnState();

  const body = requestBody(prompt, negative, size, steps, seed);
  const json = useJsonRequest(view, body);
  const curl = `${curlJson("/v1/images/generations", body)} \\\n  | jq -r '.data[0].b64_json' | base64 -d > omni.png`;

  const generate = async () => {
    const payload = view === "json" ? json.body : body;
    if (!payload?.prompt) return;
    const run = startRun();
    if (!run) return;
    const { signal, execId: id } = run;
    add({ id, at: Date.now(), request: payload, prompt: payload.prompt, pending: true });
    setMeta("generating…");
    const start = performance.now();
    try {
      const { status, data: result } = await generateImage(payload, signal, id);
      const elapsed = since(start);
      record("image", status, elapsed);
      const seedText = payload.seed != null ? ` · seed ${payload.seed}` : "";
      const caption = `${payload.size} · ${payload.steps} steps${seedText} · ${(elapsed / 1000).toFixed(1)} s`;
      const first = result.data?.[0];
      if (!first?.b64_json) throw new Error("image response had no b64_json data");
      update(id, {
        pending: false,
        url: base64ToObjectUrl(first.b64_json),
        caption,
        response: { size: first.size, seed: first.seed ?? payload.seed, steps: payload.steps, ms: elapsed },
      });
      setMeta(`${elapsed} ms`);
    } catch (error) {
      if (error.name === "AbortError") {
        setMeta("");
        update(id, { pending: false, error: "stopped" });
      } else {
        record("image", error.status ?? 0, since(start));
        update(id, { pending: false, error: error.message });
        setMeta("");
      }
    } finally {
      finish();
    }
  };

  const openId = turnState.open ?? turns[turns.length - 1]?.id;
  const randomSeed = () => setSeed(String(Math.floor(Math.random() * (SEED_MAX + 1))));

  return (
    <Workbench
      title="Image"
      subtitle="Text to image with Qwen-Image-2.1-Turbo (8 steps). First request loads it (~1 min); smaller sizes are faster."
      modelRole="image"
    >
      <Panel
        title="Request"
        endpoint="/v1/images/generations"
        tools={<UiJson view={view} onChange={setView} />}
        foot={(
          <>
            {busy
              ? <ui.Button variant="stop" class={styles.action} onClick={stop}>Stop</ui.Button>
              : <ui.Button variant="primary" class={styles.action} disabled={view === "json" ? Boolean(json.error) || !json.body?.prompt : !body.prompt} onClick={generate}>Generate</ui.Button>}
          </>
        )}
      >
        <RequestBody view={view} jsonText={json.text} onJsonText={json.setText} jsonError={json.error} curl={curl}>
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
              <div class={styles.seedRow}>
                <input type="number" step={1} aria-label="Seed" placeholder="blank = random" value={seed} onInput={(event) => setSeed(event.currentTarget.value)} />
                <ui.IconButton icon="dice" label="Random seed" onClick={randomSeed} />
              </div>
            </ui.Field>
          </ui.Grid>
          <ui.Field
            label="Steps"
            trailing={<ui.Meta>{steps}</ui.Meta>}
            hint={<span class={styles.scale}><span>{STEPS_MIN}</span><span>{STEPS_MAX}</span></span>}
          >
            <input type="range" min={STEPS_MIN} max={STEPS_MAX} value={steps} onInput={(event) => setSteps(Number(event.currentTarget.value))} />
          </ui.Field>
        </RequestBody>
      </Panel>

      <Panel
        title="Images"
        meta={meta}
        tools={<ui.IconButton icon="trash" label="Clear history" disabled={!turns.length || busy} onClick={() => { clear(); setMeta(""); }} />}
      >
        {turns.length
          ? (
            <div class={styles.gallery}>
              {turns.map((turn, index) => (
                <Turn
                  key={turn.id}
                  label={`Image #${index + 1}`}
                  at={turn.at}
                  model={turn.request.size}
                  meta={turn.caption}
                  view={turnState.viewOf(turn.id)}
                  onView={(next) => turnState.setViewOf(turn.id, next)}
                  open={openId === turn.id}
                  onToggle={() => turnState.toggle(turn.id)}
                  summary={turn.prompt}
                  wire={{ request: turn.request, response: turn.response ?? { status: turn.pending ? "generating" : turn.error ?? "done" } }}
                >
                  {turn.pending
                    ? <Pending label="generating" note={`${turn.request.size} · ${turn.request.steps} steps`} />
                    : turn.error
                      ? <TurnError>{turn.error}</TurnError>
                      : (
                        <div class={styles.figure}>
                          <img class={styles.picture} src={turn.url} alt={turn.prompt} />
                          <div class={styles.foot}>
                            <ui.Meta>{turn.caption}</ui.Meta>
                            <a class={styles.link} href={turn.url} download="omni.png">download .png</a>
                          </div>
                        </div>
                      )}
                </Turn>
              ))}
            </div>
          )
          : <ui.Empty>Generated images appear here; each can be downloaded.</ui.Empty>}
      </Panel>
      <LogsCard role="image" />
    </Workbench>
  );
}
