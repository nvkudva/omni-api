import { errorText, getJson, postJson, record } from "./client.js";
import { since } from "../lib/format.js";

// --- chat ------------------------------------------------------------------

export function chatStream(body, abortSignal) {
  return fetch("/v1/chat/completions", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
    signal: abortSignal,
  });
}

// --- language --------------------------------------------------------------

export async function decide(body, abortSignal) {
  const response = await postJson("/v1/systemone", body, { signal: abortSignal });
  return { status: response.status, data: await response.json() };
}

export async function embed(body, abortSignal) {
  const response = await postJson("/v1/embeddings", body, { signal: abortSignal });
  return { status: response.status, data: await response.json() };
}

// --- media -----------------------------------------------------------------

export async function generateImage(body, abortSignal) {
  const response = await postJson("/v1/images/generations", body, { signal: abortSignal });
  return { status: response.status, data: await response.json() };
}

export async function createVideo(body, abortSignal) {
  const response = await postJson("/v1/videos", body, { signal: abortSignal });
  return { status: response.status, data: await response.json() };
}

export function videoJob(id, abortSignal) {
  return getJson(`/v1/videos/${id}`, { signal: abortSignal });
}

// --- speech ----------------------------------------------------------------

export async function transcribe(blob, name, responseFormat = "verbose_json", abortSignal) {
  const form = new FormData();
  form.append("file", blob, name);
  form.append("response_format", responseFormat);
  const started = performance.now();
  const response = await fetch("/v1/audio/transcriptions", { method: "POST", body: form, signal: abortSignal });
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

export function synthesize(body, abortSignal) {
  return fetch("/v1/audio/speech", {
    method: "POST",
    headers: { "content-type": "application/json" },
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

export const serverInfo = () => getJson("/admin/server");
export const serverAction = (action) => postAdmin(`/admin/server/${action}`, {});
export const saveSetting = (body) => postAdmin("/admin/settings", body);
export const serverLogs = (lines) => getJson(`/admin/logs?lines=${lines}`);
