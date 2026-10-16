import { useState } from "preact/hooks";
import * as api from "../api.js";
import * as ui from "../ui.jsx";
import styles from "./tabs.module.css";

const DEFAULT_PROMPT = "A paper boat drifts down a rain-filled gutter at dusk, streetlights reflecting in the ripples, slow tracking shot, shallow depth of field.";
const SIZES = ["512x320", "704x480", "768x512", "1024x576", "1280x704", "480x704"];
const POLL_MS = 3000;

function requestBody({ prompt, negative, size, seconds, seed, frame }, shortenImage = false) {
  const body = { prompt: prompt.trim(), size, seconds, seed: Number(seed || 42) };
  if (negative.trim()) body.negative_prompt = negative.trim();
  if (frame) body.image = shortenImage ? api.shorten(frame.url) : frame.url;
  return body;
}

function caption(job, seed, start) {
  const elapsed = ((performance.now() - start) / 1000).toFixed(0);
  return `${job.status} · ${job.size} · ${job.seconds} s · seed ${seed} · ${elapsed} s`;
}

const wait = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

function Job({ job }) {
  return (
    <li class={styles.figure}>
      {job.url && <video class={styles.video} src={job.url} controls loop playsinline />}
      <p class={styles.clipText}>{job.prompt}</p>
      <div class={styles.clipFoot}>
        <ui.Meta>{job.caption}</ui.Meta>
        {job.url && <a class={styles.link} href={job.url} download={`${job.id}.mp4`}>download .mp4</a>}
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

  const edit = (field) => (event) => setForm({ ...form, [field]: event.currentTarget.value });
  const body = requestBody(form);
  const curl = `${api.curlJson("/v1/videos", requestBody(form, true))} | jq -r .id\n# poll until "status": "completed", then\ncurl -s ${api.ORIGIN}/v1/videos/$ID/content -o omni.mp4`;

  const chooseFrame = async (file) => {
    if (!file.type.startsWith("image/")) return;
    setForm({ ...form, frame: { name: file.name, url: await api.readDataUrl(file) } });
  };

  const updateJob = (key, changes) => setJobs((current) => current.map((job) => (job.key === key ? { ...job, ...changes } : job)));

  const follow = async (job, key, start) => {
    while (job.status === "queued" || job.status === "in_progress") {
      updateJob(key, { caption: caption(job, body.seed, start) });
      await wait(POLL_MS);
      job = await api.getJson(`/v1/videos/${job.id}`);
    }
    updateJob(key, { caption: caption(job, body.seed, start) });
    if (job.status === "failed") {
      updateJob(key, { error: job.error?.message ?? "generation failed" });
      return;
    }
    updateJob(key, { id: job.id, url: `/v1/videos/${job.id}/content` });
  };

  const submit = async () => {
    if (!body.prompt) return;
    const key = crypto.randomUUID();
    setBusy(true);
    setJobs((current) => [{ key, prompt: body.prompt, caption: "submitting…" }, ...current]);
    const start = performance.now();
    try {
      const response = await fetch("/v1/videos", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      });
      api.record("video", response.status, api.since(start));
      if (!response.ok) throw new Error(await api.errorText(response));
      const job = await response.json();
      setMeta(job.id);
      setBusy(false);
      await follow(job, key, start);
    } catch (error) {
      updateJob(key, { error: error.message });
    } finally {
      setBusy(false);
    }
  };

  return (
    <ui.Page title="Video" subtitle="Text or image to video, run as a background job.">
      <ui.Columns>
        <ui.Card title="Prompt" meta={api.models.value.defaults.video}>
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
                  {SIZES.map((option) => <option value={option}>{option}</option>)}
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
              ? <ul class={styles.gallery}>{jobs.map((job) => <Job key={job.key} job={job} />)}</ul>
              : <ui.Empty>Jobs queue here; finished clips play inline.</ui.Empty>}
            <ui.CurlBlock code={curl} />
          </ui.Stack>
        </ui.Card>
      </ui.Columns>
    </ui.Page>
  );
}
