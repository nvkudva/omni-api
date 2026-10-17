import { useEffect, useRef, useState } from "preact/hooks";
import { ORIGIN, readDataUrl, record } from "../api/client.js";
import { createVideo, videoJob } from "../api/endpoints.js";
import { models } from "../api/models.js";
import { runtime } from "../api/roles.js";
import { curlJson } from "../lib/curl.js";
import { shorten, since } from "../lib/format.js";
import * as ui from "../components/ui.jsx";
import { Panel, Pending, RequestBody, Turn, TurnError, UiJson, Workbench, useTurnState } from "../components/workbench.jsx";
import { LogsCard } from "../components/LogsCard.jsx";
import { useJsonRequest } from "../hooks/useJsonRequest.js";
import { GatedNotice } from "./Models.jsx";
import styles from "./Video.module.css";

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

export default function Video() {
  const [form, setForm] = useState({ prompt: DEFAULT_PROMPT, negative: "", size: "704x480", seconds: 4, seed: "42", frame: null });
  const [jobs, setJobs] = useState([]);
  const [busy, setBusy] = useState(false);
  const [meta, setMeta] = useState("");
  const [view, setView] = useState("ui");
  const controllers = useRef(new Map());
  const turnState = useTurnState();

  useEffect(() => () => {
    controllers.current.forEach((controller) => controller.abort());
    controllers.current.clear();
  }, []);

  const edit = (field) => (event) => setForm({ ...form, [field]: event.currentTarget.value });
  const body = requestBody(form);
  const json = useJsonRequest(view, requestBody(form, true));
  const curl = `${curlJson("/v1/videos", requestBody(form, true))} | jq -r .id\n# poll until "status": "completed", then\ncurl -s ${ORIGIN}/v1/videos/$ID/content -o omni.mp4`;
  const role = runtime.value.roles.video;

  const chooseFrame = async (file) => {
    if (!file.type.startsWith("image/")) return;
    setForm({ ...form, frame: { name: file.name, url: await readDataUrl(file) } });
  };

  const updateJob = (id, changes) => setJobs((current) => current.map((job) => (job.id === id ? { ...job, ...changes } : job)));

  const follow = async (job, id, start, abortSignal, seed) => {
    const deadline = start + MAX_JOB_MS;
    while (RUNNING.has(job.status)) {
      updateJob(id, { status: job.status, caption: caption(job, seed, start) });
      if (performance.now() >= deadline) {
        updateJob(id, { status: "failed", error: "timed out after 60 minutes" });
        return;
      }
      await wait(POLL_MS, abortSignal);
      if (document.hidden) continue; // don't poll a hidden tab
      job = await videoJob(job.id, abortSignal);
    }
    updateJob(id, { status: job.status, caption: caption(job, seed, start) });
    if (job.status === "failed") {
      updateJob(id, {
        error: job.error?.message ?? "generation failed",
        gatedModel: job.error?.code === "gated" || job.error?.type === "gated" ? (job.model ?? "") : "",
        acceptUrl: job.error?.accept_url ?? "",
      });
      return;
    }
    updateJob(id, { remoteId: job.id, url: `/v1/videos/${job.id}/content` });
  };

  const submit = async () => {
    const payload = view === "json" ? { ...(json.body ?? {}) } : body;
    if (!payload?.prompt) return;
    // The JSON pane shows a shortened data URL; send the real frame instead.
    if (typeof payload.image === "string" && payload.image.endsWith("…") && form.frame) payload.image = form.frame.url;

    const id = crypto.randomUUID();
    const at = Date.now();
    const controller = new AbortController();
    controllers.current.set(id, controller);
    setBusy(true);
    setJobs((current) => [{ id, at, request: payload, prompt: payload.prompt, seed: payload.seed, status: "queued", caption: "submitting…" }, ...current]);
    setMeta("submitting…");
    const start = performance.now();
    try {
      const { status, data: job } = await createVideo(payload, controller.signal);
      record("video", status, since(start));
      setMeta(job.id);
      setBusy(false);
      await follow(job, id, start, controller.signal, payload.seed);
    } catch (error) {
      if (error.name === "AbortError") updateJob(id, { status: "stopped", error: "stopped" });
      else {
        record("video", error.status ?? 0, since(start));
        updateJob(id, { status: "failed", error: error.message });
      }
    } finally {
      controllers.current.delete(id);
      setBusy(false);
    }
  };

  const stop = (id) => controllers.current.get(id)?.abort();
  const openId = turnState.open ?? jobs[0]?.id;
  const running = jobs.some((job) => RUNNING.has(job.status));

  const readout = [
    { label: "Model", value: models.value.defaults.video ?? "—" },
    { label: "Runtime", value: "ltx-mlx · 4-bit" },
    { label: "API", value: "/v1/videos" },
    { label: "Last run", value: role?.loaded ? (meta || "idle") : "not loaded" },
  ];

  return (
    <Workbench title="Video" subtitle="Text or image to video, run as a background job you can stop and poll." readout={readout}>
      <Panel
        title="Request"
        tools={<UiJson view={view} onChange={setView} />}
        foot={(
          <>
            <span class={styles.foot}>{form.size} · {form.seconds} s · seed {form.seed || 42}</span>
            <ui.Button variant="primary" disabled={busy} onClick={submit}>{busy ? "Submitting…" : "Generate"}</ui.Button>
          </>
        )}
      >
        <RequestBody view={view} jsonText={json.text} onJsonText={json.setText} jsonError={json.error} curl={curl}>
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
          <ui.Notice tone="warn">
            LTX-2.5 distilled, 4-bit MLX. The first request downloads the model pack (~51 GB) plus its Gemma text encoder, then loads for minutes. Change or pre-install it on the Models page.
          </ui.Notice>
        </RequestBody>
      </Panel>

      <Panel
        title="Jobs"
        meta={meta}
        tools={<ui.IconButton icon="trash" label="Clear jobs" disabled={!jobs.length || running} onClick={() => { setJobs([]); setMeta(""); }} />}
      >
        {jobs.length
          ? (
            <div class={styles.stack}>
              {jobs.map((job, index) => (
                <Turn
                  key={job.id}
                  label={`Job #${jobs.length - index}`}
                  at={job.at}
                  model={job.request?.size}
                  status={RUNNING.has(job.status) ? job.status : undefined}
                  meta={job.caption}
                  view={turnState.viewOf(job.id)}
                  onView={(next) => turnState.setViewOf(job.id, next)}
                  open={openId === job.id}
                  onToggle={() => turnState.toggle(job.id)}
                  summary={job.prompt}
                  wire={{ request: job.request, response: { id: job.remoteId, status: job.status, error: job.error } }}
                >
                  {RUNNING.has(job.status)
                    ? <Pending label={job.status} note={job.caption}>{<ui.Button small variant="stop" onClick={() => stop(job.id)}>Stop</ui.Button>}</Pending>
                    : job.acceptUrl
                      ? <GatedNotice model={job.gatedModel || models.value.defaults.video || "This model"} acceptUrl={job.acceptUrl} busy={busy} onRetry={submit} actionLabel="Retry generate" busyLabel="Submitting…" />
                      : job.error
                        ? <TurnError>{job.error}</TurnError>
                        : (
                          <div class={styles.stack}>
                            {job.url && <video class={styles.video} src={job.url} controls loop playsinline />}
                            <div class={styles.clipFoot}>
                              <ui.Meta>{job.caption}</ui.Meta>
                              {job.url && <a class={styles.link} href={job.url} download={`${job.remoteId ?? job.id}.mp4`}>download .mp4</a>}
                            </div>
                          </div>
                        )}
                </Turn>
              ))}
            </div>
          )
          : <ui.Empty>Jobs queue here; finished clips play inline.</ui.Empty>}
      </Panel>
      <LogsCard role="video" />
    </Workbench>
  );
}
