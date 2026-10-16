import { useEffect, useRef, useState } from "preact/hooks";
import { ORIGIN, readDataUrl, record } from "../api/client.js";
import { createVideo, videoJob } from "../api/endpoints.js";
import { models } from "../api/models.js";
import { curlJson } from "../lib/curl.js";
import { shorten, since } from "../lib/format.js";
import * as ui from "../components/ui.jsx";
import styles from "./tabs.module.css";

const DEFAULT_PROMPT = "A paper boat drifts down a rain-filled gutter at dusk, streetlights reflecting in the ripples, slow tracking shot, shallow depth of field.";
const SIZES = ["512x320", "704x480", "768x512", "1024x576", "1280x704", "480x704"];
const POLL_MS = 3000;
const MAX_JOB_MS = 60 * 60 * 1000;
const RUNNING = new Set(["queued", "in_progress", "submitted"]);

function requestBody({ prompt, negative, size, seconds, seed, frame }, shortenImage = false) {
  const body = { prompt: prompt.trim(), size, seconds, seed: Number(seed || 42) };
  if (negative.trim()) body.negative_prompt = negative.trim();
  if (frame) body.image = shortenImage ? shorten(frame.url) : frame.url;
  return body;
}

function caption(job, seed, start) {
  const elapsed = ((performance.now() - start) / 1000).toFixed(0);
  return `${job.status} · ${job.size} · ${job.seconds} s · seed ${seed} · ${elapsed} s`;
}

function wait(milliseconds, abortSignal) {
  return new Promise((resolve, reject) => {
    const onAbort = () => {
      clearTimeout(timer);
      reject(new DOMException("aborted", "AbortError"));
    };
    const timer = setTimeout(() => {
      abortSignal?.removeEventListener("abort", onAbort);
      resolve();
    }, milliseconds);
    if (abortSignal?.aborted) onAbort();
    else abortSignal?.addEventListener("abort", onAbort, { once: true });
  });
}

function Job({ job, onStop }) {
  const running = RUNNING.has(job.status);
  return (
    <li class={styles.figure}>
      {job.url && <video class={styles.video} src={job.url} controls loop playsinline />}
      <p class={styles.clipText}>{job.prompt}</p>
      <div class={styles.clipFoot}>
        <ui.Meta>{job.caption}</ui.Meta>
        <span class={styles.buttons}>
          {running && <ui.Button small variant="stop" onClick={() => onStop(job.key)}>Stop</ui.Button>}
          {job.url && <a class={styles.link} href={job.url} download={`${job.id}.mp4`}>download .mp4</a>}
        </span>
      </div>
      {job.error && <ui.ErrorText>{job.error}</ui.ErrorText>}
    </li>
  );
}

export default function Video() {
  const [form, setForm] = useState({ prompt: DEFAULT_PROMPT, negative: "", size: "704x480", seconds: 4, seed: "42", frame: null });
  const [busy, setBusy] = useState(false);
  const [jobs, setJobs] = useState([]);
  const [meta, setMeta] = useState("");
  const controllers = useRef(new Map());

  useEffect(() => () => {
    controllers.current.forEach((controller) => controller.abort());
    controllers.current.clear();
  }, []);

  const edit = (field) => (event) => setForm({ ...form, [field]: event.currentTarget.value });
  const body = requestBody(form);
  const curl = `${curlJson("/v1/videos", requestBody(form, true))} | jq -r .id\n# poll until "status": "completed", then\ncurl -s ${ORIGIN}/v1/videos/$ID/content -o omni.mp4`;

  const chooseFrame = async (file) => {
    if (!file.type.startsWith("image/")) return;
    setForm({ ...form, frame: { name: file.name, url: await readDataUrl(file) } });
  };

  const updateJob = (key, changes) => setJobs((current) => current.map((job) => (job.key === key ? { ...job, ...changes } : job)));

  const stop = (key) => controllers.current.get(key)?.abort();

  const follow = async (job, key, start, abortSignal, seed) => {
    const deadline = start + MAX_JOB_MS;
    while (RUNNING.has(job.status)) {
      updateJob(key, { status: job.status, caption: caption(job, seed, start) });
      if (performance.now() >= deadline) {
        updateJob(key, { status: "failed", error: "timed out after 60 minutes" });
        return;
      }
      await wait(POLL_MS, abortSignal);
      if (document.hidden) continue; // don't poll a hidden tab
      job = await videoJob(job.id, abortSignal);
    }
    updateJob(key, { status: job.status, caption: caption(job, seed, start) });
    if (job.status === "failed") {
      updateJob(key, { error: job.error?.message ?? "generation failed" });
      return;
    }
    updateJob(key, { id: job.id, url: `/v1/videos/${job.id}/content` });
  };

  const submit = async () => {
    if (!body.prompt) return;
    const key = crypto.randomUUID();
    const controller = new AbortController();
    controllers.current.set(key, controller);
    setBusy(true);
    setJobs((current) => [{ key, status: "queued", prompt: body.prompt, caption: "submitting…" }, ...current]);
    const start = performance.now();
    try {
      const { status, data: job } = await createVideo(body, controller.signal);
      record("video", status, since(start));
      setMeta(job.id);
      setBusy(false);
      await follow(job, key, start, controller.signal, body.seed);
    } catch (error) {
      if (error.name === "AbortError") {
        updateJob(key, { status: "stopped", error: "stopped" });
      } else {
        record("video", error.status ?? 0, since(start));
        updateJob(key, { status: "failed", error: error.message });
      }
    } finally {
      controllers.current.delete(key);
      setBusy(false);
    }
  };

  return (
    <ui.Page title="Video" subtitle="Text or image to video, run as a background job.">
      <ui.Columns>
        <ui.Card title="Prompt" meta={models.value.defaults.video}>
          <ui.Stack>
            <ui.Field label="Prompt">
              <textarea rows={4} value={form.prompt} onInput={edit("prompt")} />
            </ui.Field>
            <ui.Field label="Negative prompt">
              <input placeholder="optional" value={form.negative} onInput={edit("negative")} />
            </ui.Field>
            <ui.Grid>
              <ui.Field label="Size">
                <select value={form.size} onChange={edit("size")}>
                  {SIZES.map((option) => <option key={option} value={option}>{option}</option>)}
                </select>
              </ui.Field>
              <ui.Field label="Seed">
                <input type="number" value={form.seed} onInput={edit("seed")} />
              </ui.Field>
            </ui.Grid>
            <ui.Field label="Length" trailing={<ui.Meta>{form.seconds} s</ui.Meta>}>
              <input type="range" min={1} max={10} value={form.seconds} onInput={(event) => setForm({ ...form, seconds: Number(event.currentTarget.value) })} />
            </ui.Field>
            <ui.DropZone accept="image/*" onFile={chooseFrame}>
              {form.frame
                ? <span class={styles.frame}><img class={styles.thumb} src={form.frame.url} alt={form.frame.name} /> {form.frame.name} — click to change</span>
                : "First frame (optional): drop an image or click to choose"}
            </ui.DropZone>
            <ui.Notice tone="warn">LTX-2.5 distilled on MLX. The first request downloads the model pack (~75 GB at q8) plus its Gemma text encoder, then loads for minutes. Change or pre-install it in the Models tab.</ui.Notice>
            <ui.Row end>
              <ui.Button variant="primary" disabled={busy} onClick={submit}>{busy ? "Submitting…" : "Generate"}</ui.Button>
            </ui.Row>
          </ui.Stack>
        </ui.Card>
        <ui.Card title="Jobs" meta={meta}>
          <ui.Stack>
            {jobs.length
              ? <ul class={styles.gallery}>{jobs.map((job) => <Job key={job.key} job={job} onStop={stop} />)}</ul>
              : <ui.Empty>Jobs queue here; finished clips play inline.</ui.Empty>}
            <ui.CurlBlock code={curl} />
          </ui.Stack>
        </ui.Card>
      </ui.Columns>
    </ui.Page>
  );
}
