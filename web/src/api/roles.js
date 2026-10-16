import { signal } from "@preact/signals";
import { getJson } from "./client.js";
import { pinRole } from "./endpoints.js";
import { health, healthError } from "./health.js";

// One line per role: what it is, where its endpoint lives, which runtime serves
// it, and the accent its icon wears. This is presentation metadata, not config.
export const ROLE_META = {
  chat: { label: "Chat", endpoint: "POST /v1/chat/completions", runner: "ollama", color: "#0a84ff" },
  decide: { label: "Decide", endpoint: "POST /v1/systemone", runner: "ollama", color: "#bf5af2" },
  embed: { label: "Embed", endpoint: "POST /v1/embeddings", runner: "ollama", color: "#30b0c7" },
  stt: { label: "Speech to text", endpoint: "POST /v1/audio/transcriptions", runner: "parakeet-mlx", color: "#ff453a" },
  tts: { label: "Text to speech", endpoint: "POST /v1/audio/speech", runner: "mlx-audio", color: "#30d158" },
  image: { label: "Image", endpoint: "POST /v1/images/generations", runner: "diffusers", color: "#ff9f0a" },
  video: { label: "Video", endpoint: "POST /v1/videos", runner: "ltx-mlx", color: "#ff375f" },
};

export const ROLE_ORDER = ["chat", "decide", "embed", "stt", "tts", "image", "video"];
export const OLLAMA_ROLES = new Set(["chat", "decide", "embed"]);

export const runtime = signal({ ready: false, error: "", roles: {}, warnings: [], memory: null, pins: [] });

const resident = (ollama, name) =>
  (ollama ?? []).find((model) => model.name === name || model.name === `${name}:latest`);

/** The per-role card model: default model, whether it is resident, and its memory. */
function roleState(role, defaults, health, memory) {
  const meta = ROLE_META[role];
  const worker = (health?.workers ?? {})[role];
  const memoryWorker = (memory?.workers ?? {})[role];
  const model = defaults?.[role] ?? "—";
  // a pinned role is one the gateway is keeping resident on purpose, so its card stays on
  const pinned = (health?.pins ?? []).includes(role);

  if (OLLAMA_ROLES.has(role)) {
    // Ollama's models outlive the role: remember what it pulled, then match it.
    const running = memory?.loaded?.[role] ?? resident(memory?.ollama, model);
    return {
      ...meta,
      role,
      model,
      loaded: Boolean(running),
      pinned,
      runningModel: running?.name ?? null,
      memoryBytes: running?.size_bytes ?? 0,
      status: running ? "loaded" : "idle",
      cpuPercent: 0,
      held: false,
    };
  }

  // Worker roles report what they hold: the process can be up (ready) while its
  // model is still out of memory, so trust the worker's own `loaded` flag.
  const status = worker?.status ?? memoryWorker?.status ?? "stopped";
  const loaded = Boolean(worker?.loaded);
  return {
    ...meta,
    role,
    model,
    loaded,
    pinned,
    runningModel: loaded ? (worker?.model || model) : null,
    memoryBytes: loaded ? (worker?.rss_bytes ?? memoryWorker?.rss_bytes ?? 0) : 0,
    status,
    cpuPercent: memoryWorker?.cpu_percent ?? worker?.cpu_percent ?? 0,
    held: Boolean(memoryWorker?.held ?? worker?.held),
  };
}

// `/admin/memory` reports Ollama's resident models but only under one flat list.
// A role's default is the best guess at which of them it is using, unless the
// model name is one another role already claimed.
function attributeOllama(memory, defaults) {
  const claimed = new Set();
  const loaded = {};
  for (const role of ROLE_ORDER) {
    if (!OLLAMA_ROLES.has(role)) continue;
    const found = resident(memory.ollama, defaults?.[role]);
    if (found && !claimed.has(found.name)) {
      loaded[role] = found;
      claimed.add(found.name);
    }
  }
  return { ...memory, loaded };
}

export async function pollRuntime() {
  try {
    const [healthResponse, memory, models] = await Promise.all([
      getJson("/health"),
      getJson("/admin/memory").catch(() => null),
      getJson("/v1/models").catch(() => null),
    ]);
    // One poll feeds both the sidebar cards and the gateway badge.
    health.value = healthResponse;
    healthError.value = "";
    const defaults = models?.defaults ?? {};
    const attributed = memory ? attributeOllama(memory, defaults) : null;
    const roles = {};
    for (const role of ROLE_ORDER) roles[role] = roleState(role, defaults, healthResponse, attributed);
    runtime.value = {
      ready: true,
      error: "",
      roles,
      warnings: memory?.warnings ?? [],
      memory: attributed,
      pins: healthResponse.pins ?? [],
    };
    return runtime.value;
  } catch (error) {
    healthError.value = error.message;
    runtime.value = { ...runtime.value, ready: false, error: error.message };
    return runtime.value;
  }
}

/**
 * Keep a role's model resident, or stop keeping it and unload it. A refusal about the model itself
 * comes back as a 409 with `blockers`; retry with `force: true` once the person has seen them.
 */
export async function setPinned(role, pinned, { force = false } = {}) {
  const model = runtime.value.roles[role]?.model;
  const result = await pinRole({ role, model, pinned, force });
  await pollRuntime();
  return result;
}
