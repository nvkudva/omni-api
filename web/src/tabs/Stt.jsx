import { useEffect, useRef, useState } from "preact/hooks";
import { ORIGIN } from "../api/client.js";
import { transcribe } from "../api/endpoints.js";
import { clock } from "../lib/format.js";
import * as ui from "../components/ui.jsx";
import styles from "./tabs.module.css";

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

function Transcript({ state, audio }) {
  if (!state) return <ui.Empty>Recording stops → transcribes automatically. Click a segment to play from there.</ui.Empty>;
  if (state.pending) return <ui.Empty>Transcribing…</ui.Empty>;
  if (state.error) return <ui.ErrorText>{state.error}</ui.ErrorText>;
  if (state.format === "text") return <p class={styles.transcript}>{state.result}</p>;
  const seek = (segment) => {
    if (!audio.current) return;
    audio.current.currentTime = segment.start;
    audio.current.play();
  };
  return (
    <>
      <p class={styles.transcript}>{state.result.text}</p>
      {state.result.segments?.length > 0 && (
        <ul class={styles.segments}>
          {state.result.segments.map((segment, index) => (
            <li key={index}>
              <button type="button" class={styles.segment} onClick={() => seek(segment)}>
                <span class={styles.timestamp}>{segment.start.toFixed(2)}–{segment.end.toFixed(2)}</span>
                <span>{segment.text}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
      <ui.Code>{JSON.stringify(state.result, null, 2)}</ui.Code>
    </>
  );
}

export default function Stt() {
  const [format, setFormat] = useState("verbose_json");
  const [clip, setClip] = useState(null);
  const [output, setOutput] = useState(null);
  const [busy, setBusy] = useState(false);
  const [meta, setMeta] = useState("");
  const [recording, setRecording] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [micError, setMicError] = useState("");
  const recorder = useRef(null);
  const canvas = useRef(null);
  const audio = useRef(null);
  const clipRef = useRef(null);
  const formatRef = useRef(format);
  const abort = useRef(null);
  const alive = useRef(true);
  formatRef.current = format;

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

  const run = async (blob, name) => {
    if (!blob) return;
    const responseFormat = formatRef.current;
    const controller = new AbortController();
    abort.current = controller;
    setBusy(true);
    setOutput({ pending: true });
    setMeta("");
    try {
      const { result, elapsed: ms } = await transcribe(blob, name, responseFormat, controller.signal);
      setOutput({ result, format: responseFormat });
      const duration = result?.duration;
      setMeta(duration
        ? `${ms} ms · ${duration.toFixed(1)} s audio · ${(duration / (ms / 1000)).toFixed(1)}× realtime`
        : `${ms} ms`);
    } catch (error) {
      setOutput(error.name === "AbortError" ? null : { error: error.message });
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
      run(blob, name);
    };
    media.start();
    recorder.current = media;
    setElapsed(0);
    setRecording(true);
  };

  const toggle = () => (recorder.current ? recorder.current.stop() : start());
  const current = clip;
  const curl = `curl -s ${ORIGIN}/v1/audio/transcriptions \\\n  -F file=@${current?.name ?? "speech.wav"} \\\n  -F response_format=${format}`;

  return (
    <ui.Page title="Speech to text" subtitle="Record or upload audio, get a transcript with segment timings.">
      <ui.Columns>
        <ui.Card title="Audio">
          <ui.Stack>
            <ui.Field label="Response format">
              <ui.Segmented
                label="Response format"
                options={FORMATS.map((value) => ({ value, label: value }))}
                value={format}
                onChange={setFormat}
              />
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
            {current && (
              <ui.Field label={`${current.name} · ${(current.blob.size / 1024).toFixed(0)} KB`}>
                <audio ref={audio} class={styles.audio} controls src={current.url} />
              </ui.Field>
            )}
            <ui.Row end>
              <ui.Button variant="primary" disabled={!current || busy} onClick={() => run(current.blob, current.name)}>
                {busy ? "Transcribing…" : "Transcribe"}
              </ui.Button>
            </ui.Row>
          </ui.Stack>
        </ui.Card>
        <ui.Card title="Transcript" meta={meta}>
          <ui.Stack>
            <Transcript state={output} audio={audio} />
            <ui.CurlBlock code={curl} />
          </ui.Stack>
        </ui.Card>
      </ui.Columns>
    </ui.Page>
  );
}
