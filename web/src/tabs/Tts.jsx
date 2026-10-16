import { useEffect, useRef, useState } from "preact/hooks";
import { errorText, record } from "../api/client.js";
import { synthesize, transcribe, voices as fetchVoices } from "../api/endpoints.js";
import { curlJson } from "../lib/curl.js";
import { since, wavSeconds } from "../lib/format.js";
import { usePolling } from "../hooks/usePolling.js";
import * as store from "../store.js";
import * as ui from "../components/ui.jsx";
import styles from "./tabs.module.css";

const ACCENTS = { a: "American", b: "British" };
const GENDERS = { f: "female", m: "male" };
const DEFAULT_TEXT = "Hello from omni. Every model on this machine now answers on a single port, and each one runs in its own process.";
const MAX_LENGTH = 4096;
const MAX_CLIPS = 20;

function voiceLabel(voice) {
  return `${voice.slice(3)} · ${ACCENTS[voice[0]] ?? voice[0]} ${GENDERS[voice[1]] ?? voice[1]}`;
}

function useVoices() {
  const [voices, setVoices] = useState(null);
  const [fallback, setFallback] = useState("");
  usePolling(async () => {
    const result = await fetchVoices();
    setVoices(result.voices);
    setFallback(result.default);
  }, 3000, { active: voices === null });
  return { voices, fallback };
}

function Clip({ clip }) {
  if (clip.pending) return <li class={styles.clip}><ui.Empty>Speaking… the first request loads the voice model, which can take a minute.</ui.Empty></li>;
  if (clip.error) return <li class={styles.clip}><ui.ErrorText>{clip.error}</ui.ErrorText></li>;
  return (
    <li class={styles.clip}>
      <p class={styles.clipText}>{clip.text}</p>
      <audio class={styles.audio} controls autoplay src={clip.url} />
      <div class={styles.clipFoot}>
        <ui.Meta>{clip.caption}</ui.Meta>
        <a class={styles.link} href={clip.url} download={clip.filename}>download .wav</a>
      </div>
      {clip.heard != null && <ui.Meta>stt heard: “{clip.heard.trim()}”</ui.Meta>}
    </li>
  );
}

export default function Tts() {
  const { voices, fallback } = useVoices();
  const [chosen, setChosen] = useState(() => store.get("omni.tts.voice", ""));
  const [text, setText] = useState(DEFAULT_TEXT);
  const [speed, setSpeed] = useState(1);
  const [roundTrip, setRoundTrip] = useState(false);
  const [busy, setBusy] = useState(false);
  const [clips, setClips] = useState([]);
  const [meta, setMeta] = useState("");
  const clipsRef = useRef([]);
  const abort = useRef(null);

  useEffect(() => () => {
    abort.current?.abort();
    clipsRef.current.forEach((clip) => clip.url && URL.revokeObjectURL(clip.url));
  }, []);

  // A persisted voice may no longer exist server-side: reset it to the default.
  useEffect(() => {
    if (!voices) return;
    if (chosen && voices.includes(chosen)) return;
    const next = fallback && voices.includes(fallback) ? fallback : voices[0] ?? "";
    setChosen(next);
    store.set("omni.tts.voice", next);
  }, [voices, fallback, chosen]);

  // Cap retained clips and revoke the blob URLs we drop.
  const commit = (next) => {
    const kept = next.slice(0, MAX_CLIPS);
    for (const dropped of next.slice(MAX_CLIPS)) {
      if (dropped.url) URL.revokeObjectURL(dropped.url);
    }
    clipsRef.current = kept;
    setClips(kept);
  };
  const updateClip = (id, changes) => commit(clipsRef.current.map((clip) => (clip.id === id ? { ...clip, ...changes } : clip)));

  const voice = voices ? chosen || fallback : "";
  const body = { input: text, speed, response_format: "wav" };
  if (voice) body.voice = voice;
  const curl = `${curlJson("/v1/audio/speech", body)} \\\n  -o speech.wav`;

  const choose = (name) => {
    setChosen(name);
    store.set("omni.tts.voice", name);
  };

  const speak = async () => {
    if (!body.input.trim()) return;
    const id = crypto.randomUUID();
    const controller = new AbortController();
    abort.current = controller;
    setBusy(true);
    commit([{ id, pending: true }, ...clipsRef.current]);
    const start = performance.now();
    try {
      const response = await synthesize(body, controller.signal);
      const elapsed = since(start);
      record("tts", response.status, elapsed);
      if (!response.ok) throw new Error(await errorText(response));
      const buffer = await response.arrayBuffer();
      const blob = new Blob([buffer], { type: "audio/wav" });
      const seconds = wavSeconds(buffer);
      updateClip(id, {
        pending: false,
        text: body.input,
        url: URL.createObjectURL(blob),
        filename: `omni-${body.voice ?? "speech"}.wav`,
        caption: `${body.voice ?? "default"} · ${body.speed}× · ${seconds.toFixed(1)} s audio in ${elapsed} ms (${(seconds / (elapsed / 1000)).toFixed(1)}× realtime)`,
      });
      setMeta(`${elapsed} ms`);
      if (!roundTrip) return;
      const { result } = await transcribe(blob, "speech.wav", "text", controller.signal);
      const heard = typeof result === "string" ? result : result?.text;
      if (heard != null) updateClip(id, { heard });
    } catch (error) {
      if (error.name === "AbortError") updateClip(id, { pending: false, error: "cancelled" });
      else updateClip(id, { pending: false, error: error.message });
    } finally {
      abort.current = null;
      setBusy(false);
    }
  };

  return (
    <ui.Page title="Text to speech" subtitle="Kokoro voices on MLX, returned as WAV.">
      <ui.Columns>
        <ui.Card title="Speech">
          <ui.Stack>
            <ui.Field label="Voice">
              <select value={voice} onChange={(event) => choose(event.currentTarget.value)}>
                {voices
                  ? voices.map((name) => <option key={name} value={name}>{voiceLabel(name)}</option>)
                  : <option value="">tts not ready — retrying</option>}
              </select>
            </ui.Field>
            <ui.Field label="Text" trailing={<ui.Meta>{text.length} / {MAX_LENGTH}</ui.Meta>}>
              <textarea rows={5} maxLength={MAX_LENGTH} value={text} onInput={(event) => setText(event.currentTarget.value)} />
            </ui.Field>
            <ui.Field label="Speed" trailing={<ui.Meta>{speed.toFixed(2)}×</ui.Meta>}>
              <input type="range" min={0.5} max={2} step={0.05} value={speed} onInput={(event) => setSpeed(Number(event.currentTarget.value))} />
            </ui.Field>
            <ui.Toggle checked={roundTrip} onChange={setRoundTrip}>send the result to speech → text (round trip)</ui.Toggle>
            <ui.Row end>
              {busy
                ? <ui.Button variant="stop" onClick={() => abort.current?.abort()}>Stop</ui.Button>
                : <ui.Button variant="primary" onClick={speak}>Speak</ui.Button>}
            </ui.Row>
          </ui.Stack>
        </ui.Card>
        <ui.Card title="Clips" meta={meta}>
          <ui.Stack>
            {clips.length
              ? <ul class={styles.clips}>{clips.map((clip) => <Clip key={clip.id} clip={clip} />)}</ul>
              : <ui.Empty>Generated audio plays here; each clip can be downloaded.</ui.Empty>}
            <ui.CurlBlock code={curl} />
          </ui.Stack>
        </ui.Card>
      </ui.Columns>
    </ui.Page>
  );
}
