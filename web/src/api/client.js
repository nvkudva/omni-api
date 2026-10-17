import { signal } from "@preact/signals";

export const ORIGIN = location.origin;

export const lastRequest = signal(null);

export async function errorText(response) {
  return parseError(await response.text()).message;
}

// The error body is `{"error": {"message", "type", ...}}`. The type is worth keeping: it tells a
// caller whether a refusal is forceable (`overload`) or final (`conflict`), and a refusal may also
// carry the blockers themselves.
function parseError(body) {
  try {
    const parsed = JSON.parse(body);
    const detail = parsed.error?.message ?? parsed.detail ?? body;
    const message = Array.isArray(detail)
      ? detail.map((item) => item?.msg ?? JSON.stringify(item)).join("; ")
      : detail;
    return { message, code: parsed.error?.type ?? "", blockers: parsed.error?.blockers ?? [] };
  } catch {
    return { message: body, code: "", blockers: [] };
  }
}

export function record(route, status, elapsed) {
  lastRequest.value = { route, status, elapsed };
}

async function assertOk(response) {
  if (response.ok) return response;
  const { message, code, blockers } = parseError(await response.text());
  const error = new Error(message);
  error.status = response.status;
  error.code = code;
  error.blockers = blockers;
  throw error;
}

export async function postJson(path, body, { signal: abortSignal } = {}) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
    signal: abortSignal,
  });
  return assertOk(response);
}

export async function getJson(path, { signal: abortSignal } = {}) {
  const response = await assertOk(await fetch(path, { signal: abortSignal }));
  return response.json();
}

export async function getText(path, { signal: abortSignal } = {}) {
  const response = await assertOk(await fetch(path, { signal: abortSignal }));
  return response.text();
}

// Parse one JSON frame, skipping malformed ones instead of killing the stream.
function tryParse(text) {
  try {
    return { ok: true, value: JSON.parse(text) };
  } catch {
    return { ok: false };
  }
}

export async function* sseEvents(response) {
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  const emit = function* (line) {
    if (!line.startsWith("data: ")) return;
    const data = line.slice(6);
    if (data === "[DONE]") return;
    const parsed = tryParse(data);
    if (parsed.ok) yield parsed.value;
  };
  try {
    let done = false;
    while (!done) {
      const read = await reader.read();
      done = read.done;
      if (done) break;
      buffer += read.value;
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";
      for (const line of lines) {
        if (line.startsWith("data: ") && line.slice(6) === "[DONE]") return;
        yield* emit(line);
      }
    }
    if (buffer) {
      if (buffer.startsWith("data: ") && buffer.slice(6) === "[DONE]") return;
      yield* emit(buffer);
    }
  } finally {
    try {
      reader.releaseLock();
    } catch {
      // reader already released
    }
  }
}

export async function* ndjson(response) {
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  try {
    let done = false;
    while (!done) {
      const read = await reader.read();
      done = read.done;
      if (done) break;
      buffer += read.value;
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";
      for (const line of lines) {
        if (!line.trim()) continue;
        const parsed = tryParse(line);
        if (parsed.ok) yield parsed.value;
      }
    }
    if (buffer.trim()) {
      const parsed = tryParse(buffer);
      if (parsed.ok) yield parsed.value;
    }
  } finally {
    try {
      reader.releaseLock();
    } catch {
      // reader already released
    }
  }
}

export function readDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}
