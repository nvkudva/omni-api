import { signal } from "@preact/signals";

export const ORIGIN = location.origin;
export const GB = 1024 ** 3;

export const lastRequest = signal(null);
export const models = signal({ data: [], defaults: {} });

export const gb = (bytes) => `${(bytes / GB).toFixed(1)} GB`;
export const since = (start) => Math.round(performance.now() - start);

export async function errorText(response) {
  const body = await response.text();
  try {
    const parsed = JSON.parse(body);
    return parsed.error?.message ?? parsed.detail ?? body;
  } catch {
    return body;
  }
}

export function record(route, status, elapsed) {
  lastRequest.value = { route, status, elapsed };
}

export async function postJson(path, body) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(await errorText(response));
  return response;
}

export async function getJson(path) {
  const response = await fetch(path);
  if (!response.ok) throw new Error(await errorText(response));
  return response.json();
}

export async function loadModels() {
  try {
    models.value = await getJson("/v1/models");
  } catch {
    models.value = { data: [], defaults: {} };
  }
}

export function shellQuote(text) {
  return `'${text.replaceAll("'", `'\\''`)}'`;
}

export function curlJson(path, body, extra = "") {
  return `curl -s ${ORIGIN}${path} \\\n  -H 'content-type: application/json' \\\n  -d ${shellQuote(JSON.stringify(body, null, 2))}${extra}`;
}

export async function* sseEvents(response) {
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) return;
    buffer += value;
    const lines = buffer.split("\n");
    buffer = lines.pop();
    for (const line of lines) {
      if (!line.startsWith("data: ")) continue;
      const data = line.slice(6);
      if (data === "[DONE]") return;
      yield JSON.parse(data);
    }
  }
}

export async function* ndjson(response) {
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += value;
    const lines = buffer.split("\n");
    buffer = lines.pop();
    for (const line of lines) if (line.trim()) yield JSON.parse(line);
  }
  if (buffer.trim()) yield JSON.parse(buffer);
}

export function readDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

export function shorten(url) {
  return `${url.slice(0, 40)}…`;
}

export const health = signal(null);

export async function pollHealth() {
  try {
    health.value = await getJson("/health");
  } catch {
    health.value = { ok: false, workers: {} };
  }
}
