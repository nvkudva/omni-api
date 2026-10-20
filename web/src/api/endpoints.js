import { errorText, getJson, postJson, record } from "./client.js";
import { execHeaders } from "./execs.js";
import { since } from "../lib/format.js";

// --- chat ------------------------------------------------------------------

export function chatStream(body, abortSignal, execId) {
  return fetch("/v1/chat/completions", {
    method: "POST",
    headers: { "content-type": "application/json", ...execHeaders(execId) },
    body: JSON.stringify(body),
    signal: abortSignal,
  });
}

// --- language --------------------------------------------------------------

export async function decide(body, abortSignal, execId) {
  const response = await postJson("/v1/systemone", body, { signal: abortSignal, headers: execHeaders(execId) });
  return { status: response.status, data: await response.json() };
}

export async function embed(body, abortSignal, execId) {
  const response = await postJson("/v1/embeddings", body, { signal: abortSignal, headers: execHeaders(execId) });
  return { status: response.status, data: await response.json() };
}

export async function describeImage(dataUrl, model, abortSignal, execId) {
  const response = await postJson("/v1/chat/completions", {
    model,
    stream: false,
    messages: [{
      role: "user",
      content: [
        { type: "text", text: "Describe this image for a search index: the subjects, setting, any visible text (transcribed exactly), and anything distinctive. Plain prose, no preamble." },
        { type: "image_url", image_url: { url: dataUrl } },
      ],
    }],
  }, { signal: abortSignal, headers: execHeaders(execId) });
  const data = await response.json();
  return data.choices?.[0]?.message?.content ?? "";
}

// --- media -----------------------------------------------------------------

export async function generateImage(body, abortSignal, execId) {
  const response = await postJson("/v1/images/generations", body, { signal: abortSignal, headers: execHeaders(execId) });
  return { status: response.status, data: await response.json() };
}

export async function createVideo(body, abortSignal, execId) {
  const response = await postJson("/v1/videos", body, { signal: abortSignal, headers: execHeaders(execId) });
  return { status: response.status, data: await response.json() };
}

export function videoJob(id, abortSignal) {
  return getJson(`/v1/videos/${id}`, { signal: abortSignal });
}

// --- speech ----------------------------------------------------------------

export async function transcribe(blob, name, responseFormat = "verbose_json", abortSignal, execId) {
  const form = new FormData();
  form.append("file", blob, name);
  form.append("response_format", responseFormat);
  const started = performance.now();
  const response = await fetch("/v1/audio/transcriptions", { method: "POST", body: form, signal: abortSignal, headers: execHeaders(execId) });
  const elapsed = since(started);
  record("stt", response.status, elapsed);
  if (!response.ok) {
    const error = new Error(await errorText(response));
    error.status = response.status;
    throw error;
  }
  const result = responseFormat === "text" ? await response.text() : await response.json();
  return { result, elapsed, status: response.status };
}

export function synthesize(body, abortSignal, execId) {
  return fetch("/v1/audio/speech", {
    method: "POST",
    headers: { "content-type": "application/json", ...execHeaders(execId) },
    body: JSON.stringify(body),
    signal: abortSignal,
  });
}

export function voices(abortSignal) {
  return getJson("/v1/audio/voices", { signal: abortSignal });
}

// --- admin -----------------------------------------------------------------

export function adminModels(abortSignal) {
  return getJson("/admin/models", { signal: abortSignal });
}

export function modelStatus(abortSignal) {
  return getJson("/admin/models/status", { signal: abortSignal });
}

export async function checkModel(params) {
  return getJson(`/admin/models/check?${params}`);
}

export function memory(abortSignal) {
  return getJson("/admin/memory", { signal: abortSignal });
}

export async function searchModels(params) {
  return getJson(`/admin/models/search?${params}`);
}

const postAdmin = async (path, body) => (await postJson(path, body)).json();

export const setRoleDefault = (body) => postAdmin("/admin/models/default", body);
export const loadRole = (body) => postAdmin("/admin/models/load", body);
export const unloadRole = (body) => postAdmin("/admin/models/unload", body);
export const pinRole = (body) => postAdmin("/admin/pins", body);
export const deleteModel = (body) => postAdmin("/admin/models/delete", body);

export async function pullModel(body, abortSignal) {
  return postJson("/admin/models/pull", body, { signal: abortSignal });
}

export const workerAction = (name, action) => postAdmin(`/admin/workers/${name}/${action}`, {});

export const workersAction = (action) => postAdmin(`/admin/workers/all/${action}`, {});

export const serverInfo = () => getJson("/admin/server");
export const serverAction = (action) => postAdmin(`/admin/server/${action}`, {});
export const saveSetting = (body) => postAdmin("/admin/settings", body);
export const serverLogs = (lines) => getJson(`/admin/logs?lines=${lines}`);
