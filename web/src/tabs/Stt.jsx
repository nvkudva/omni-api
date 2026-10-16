import { useEffect, useRef, useState } from "preact/hooks";
import { transcribe } from "../api/endpoints.js";
import { models } from "../api/models.js";
import { runtime } from "../api/roles.js";
import { clock } from "../lib/format.js";
import * as ui from "../components/ui.jsx";
import { Panel, Pending, RequestBody, Turn, TurnError, UiJson, Workbench, useTurnState } from "../components/workbench.jsx";
import { useJsonRequest } from "../hooks/useJsonRequest.js";
import styles from "./Stt.module.css";

const FORMATS = ["verbose_json", "json", "text"];

function drawWave(canvas, analyser) {
  const context = canvas.getContext("2d");
  const samples = new Uint8Array(analyser.fftSize);
  const color = getComputedStyle(document.documentElement).getPropertyValue("--signal");
  let frame = 0;
  const draw = () => {
    analyser.getByteTimeDomainData(samples);
    context.clearRect(0, 0, canvas.width, canvas.height);
    context.strokeStyle = color;
    context.lineWidth = 1.5;
    context.beginPath();
    for (let index = 0; index < samples.length; index++) {
      const x = (index / samples.length) * canvas.width;
      const y = (samples[index] / 255) * canvas.height;
      if (index === 0) context.moveTo(x, y);
      else context.lineTo(x, y);
    }
    context.stroke();
    frame = requestAnimationFrame(draw);
  };
  draw();
  return () => cancelAnimationFrame(frame);
}

/** The transcript of one clip, with its own player so segments can seek it. */
function Transcript({ turn }) {
  const audio = useRef(null);
  const [copied, setCopied] = useState(false);

  const seek = (segment) => {
    const element = audio.current;
    if (!element) return;
    element.currentTime = segment.start;
    element.play();
  };
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(turn.format === "text" ? turn.result : turn.result.text ?? "");
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    } catch {
      // clipboard is best-effort
    }
  };

  if (turn.format === "text") {
    return (
      <div class={styles.stack}>
        {turn.url && <audio ref={audio} class={styles.audio} controls src={turn.url} />}
        <p class={styles.transcript}>{turn.result}</p>
        <ui.Row end><ui.Button small variant="plain" onClick={copy}>{copied ? "Copied" : "Copy text"}</ui.Button></ui.Row>
      </div>
    );
  }

  return (
    <div class={styles.stack}>
      {turn.url && <audio ref={audio} class={styles.audio} controls src={turn.url} />}
      <p class={styles.transcript}>{turn.result.text}</p>
      {turn.result.segments?.length > 0 && (
        <ul class={styles.segments}>
          {turn.result.segments.map((segment, index) => (
            <li key={index}>
              <button type="button" class={styles.segment} onClick={() => seek(segment)}>
                <span class={styles.timestamp}>{segment.start.toFixed(2)}–{segment.end.toFixed(2)}</span>
                <span>{segment.text}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
      <ui.Row end><ui.Button small variant="plain" onClick={copy}>{copied ? "Copied" : "Copy text"}</ui.Button></ui.Row>
    </div>
  );
}

export default function Stt() {
  const [format, setFormat] = useState("verbose_json");
  const [clip, setClip] = useState(null);
  const [turns, setTurns] = useState([]);
  const [busy, setBusy] = useState(false);
  const [meta, setMeta] = useState("");
  const [recording, setRecording] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [micError, setMicError] = useState("");
  const [view, setView] = useState("ui");
  const recorder = useRef(null);
  const canvas = useRef(null);
  const clipRef = useRef(null);
  const abort = useRef(null);
  const alive = useRef(true);
  const turnState = useTurnState();

  useEffect(() => () => {
    alive.current = false;
    recorder.current?.stop();
    abort.current?.abort();
    if (clipRef.current?.url) URL.revokeObjectURL(clipRef.current.url);
  }, []);

  const applyClip = (blob, name) => {
    if (clipRef.current?.url) URL.revokeObjectURL(clipRef.current.url);
    const next = { blob, name, url: URL.createObjectURL(blob) };
    clipRef.current = next;
    setClip(next);
  };

  const run = async (blob, name, responseFormat) => {
    if (!blob) return;
    const controller = new AbortController();
    abort.current = controller;
    const id = crypto.randomUUID();
    const at = Date.now();
    const url = clipRef.current?.blob === blob ? clipRef.current.url : URL.createObjectURL(blob);
    setBusy(true);
    setTurns((current) => [{ id, at, url, request: { file: name, response_format: responseFormat }, pending: true }, ...current]);
    setMeta("transcribing…");
    try {
      const { result, elapsed: ms } = await transcribe(blob, name, responseFormat, controller.signal);
      const duration = result?.duration;
      const caption = duration
        ? `${ms} ms · ${duration.toFixed(1)} s audio · ${(duration / (ms / 1000)).toFixed(1)}× realtime`
        : `${ms} ms`;
      setTurns((current) => current.map((turn) => (turn.id === id
        ? { ...turn, pending: false, result, format: responseFormat, meta: caption }
        : turn)));
      setMeta(caption);
    } catch (error) {
      setTurns((current) => current.map((turn) => (turn.id === id
        ? { ...turn, pending: false, error: error.name === "AbortError" ? "stopped" : error.message }
        : turn)));
      setMeta("");
    } finally {
      abort.current = null;
      setBusy(false);
    }
  };

  const start = async () => {
    setMicError("");
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (error) {
      setMicError(`microphone: ${error.message}`);
      return;
    }
    const audioContext = new AudioContext();
    const analyser = audioContext.createAnalyser();
    analyser.fftSize = 1024;
    audioContext.createMediaStreamSource(stream).connect(analyser);
    const stopWave = drawWave(canvas.current, analyser);
    const startedAt = performance.now();
    const timer = setInterval(() => setElapsed((performance.now() - startedAt) / 1000), 200);

    const chunks = [];
    const media = new MediaRecorder(stream);
    media.ondataavailable = (event) => chunks.push(event.data);
    media.onstop = () => {
      stopWave();
      clearInterval(timer);
      stream.getTracks().forEach((track) => track.stop());
      audioContext.close();
      recorder.current = null;
      setRecording(false);
      if (!alive.current) return;
      const type = media.mimeType || "audio/webm";
      const extension = type.includes("mp4") ? "m4a" : type.includes("ogg") ? "ogg" : "webm";
      const name = `recording.${extension}`;
      const blob = new Blob(chunks, { type });
      applyClip(blob, name);
      run(blob, name, format);
    };
    media.start();
    recorder.current = media;
    setElapsed(0);
    setRecording(true);
  };

  const toggle = () => (recorder.current ? recorder.current.stop() : start());

  const body = { file: clip?.name ?? "speech.wav", response_format: format };
  const json = useJsonRequest(view, body);
  const sendFormat = view === "json" ? (json.body?.response_format ?? format) : format;
  const canTranscribe = Boolean(clip) && !busy && !(view === "json" && json.error);
  const openId = turnState.open ?? turns[0]?.id;

  const readout = [
    { label: "Model", value: models.value.defaults.stt ?? "—" },
    { label: "Runtime", value: "parakeet-mlx" },
    { label: "API", value: "POST /v1/audio/transcriptions" },
    { label: "Last run", value: runtime.value.roles.stt?.loaded ? (meta || "idle") : "not loaded" },
  ];

  return (
    <Workbench title="Speech to text" subtitle="Record or upload audio, get a transcript with segment timings you can click to replay." readout={readout}>
      <Panel
        title="Request"
        meta={clip ? `${clip.name} · ${(clip.blob.size / 1024).toFixed(0)} KB` : "no clip"}
        tools={<UiJson view={view} onChange={setView} />}
        foot={(
          <>
            <span style={{ marginRight: "auto", color: "var(--text-3)", fontSize: "12px" }}>{recording ? clock(elapsed) : format}</span>
            {busy
              ? <ui.Button variant="stop" onClick={() => abort.current?.abort()}>Stop</ui.Button>
              : <ui.Button variant="primary" disabled={!canTranscribe} onClick={() => run(clip.blob, clip.name, sendFormat)}>Transcribe</ui.Button>}
          </>
        )}
      >
        <RequestBody view={view} jsonText={json.text} onJsonText={json.setText} jsonError={json.error}>
          <ui.Field label="Response format">
            <ui.Segmented label="Response format" options={FORMATS.map((value) => ({ value, label: value }))} value={format} onChange={setFormat} />
          </ui.Field>
          <div class={styles.recorder}>
            <ui.Button variant={recording ? "stop" : "secondary"} onClick={toggle}>{recording ? "■ Stop" : "● Record"}</ui.Button>
            <span class={styles.timer}>{clock(elapsed)}</span>
            <canvas ref={canvas} class={styles.wave} width={600} height={48} />
          </div>
          {micError && <ui.ErrorText>{micError}</ui.ErrorText>}
          <ui.DropZone accept="audio/*,video/*" onFile={(file) => applyClip(file, file.name)}>
            Drop audio here or click to choose (wav, mp3, m4a, webm, ogg…)
          </ui.DropZone>
          {clip && <audio class={styles.audio} controls src={clip.url} />}
        </RequestBody>
      </Panel>

      <Panel
        title="Transcripts"
        meta={meta}
        tools={<ui.Button small variant="plain" disabled={!turns.length || busy} onClick={() => { setTurns([]); setMeta(""); }}>Clear</ui.Button>}
      >
        {turns.length
          ? (
            <div class={styles.stack}>
              {turns.map((turn, index) => (
                <Turn
                  key={turn.id}
                  label={`Transcript #${turns.length - index}`}
                  at={turn.at}
                  model={models.value.defaults.stt}
                  meta={turn.meta}
                  view={turnState.viewOf(turn.id)}
                  onView={(next) => turnState.setViewOf(turn.id, next)}
                  open={openId === turn.id}
                  onToggle={() => turnState.toggle(turn.id)}
                  summary={turn.pending ? "transcribing…" : turn.request.file}
                  wire={{ request: turn.request, response: turn.error ? { error: turn.error } : turn.result }}
                >
                  {turn.pending
                    ? <Pending label="transcribing" note={turn.request.file} />
                    : turn.error
                      ? <TurnError>{turn.error}</TurnError>
                      : <Transcript turn={turn} />}
                </Turn>
              ))}
            </div>
          )
          : <ui.Empty>Recording stops → transcribes automatically. Click a segment to play from there.</ui.Empty>}
      </Panel>
    </Workbench>
  );
}
