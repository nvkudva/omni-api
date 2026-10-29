import { signal } from "@preact/signals";
import { getJson, ndjson } from "./client.js";
import { pinRole, pullModel, stopPull } from "./endpoints.js";
import { health, healthError } from "./health.js";
import { gb } from "../lib/format.js";

// One line per role: what it is, where its endpoint lives, and the accent its
// icon wears. This is presentation metadata only; what runs a role (runner,
// engine, accepted repos, recommendations) comes from the server in `specs`.
// The accent is a token, so one role reads as a vivid cyan on the dark page
// and a deep teal in daylight. The literal order IS the role order: the mini
// cards' tabs, the Settings list and the Models page all read it back through
// ROLE_ORDER below.
export const ROLE_META = {
  chat: { label: "Chat", endpoint: "/v1/chat/completions", color: "var(--role-chat)" },
  decide: { label: "Decision", endpoint: "/v1/systemone", color: "var(--role-decide)" },
  embed: { label: "Embed", endpoint: "/v1/embeddings", color: "var(--role-embed)" },
  image: { label: "Image", endpoint: "/v1/images/generations", color: "var(--role-image)" },
  video: { label: "Video", endpoint: "/v1/videos", color: "var(--role-video)" },
  stt: { label: "Speech to text", endpoint: "/v1/audio/transcriptions", color: "var(--role-stt)" },
  tts: { label: "Text to speech", endpoint: "/v1/audio/speech", color: "var(--role-tts)" },
};

export const ROLE_ORDER = Object.keys(ROLE_META);
// The server's description of each role (`/admin/models/status`): runner, engine, kind, family,
// accepts, example and the recommended models with their fit on this machine.
export const specs = signal({});
export const isOllama = (role) => specs.value[role]?.runner === "ollama";
// The roles omni runs and supervises itself: only these can be started and stopped from a card.
export const isWorker = (role) => Boolean(specs.value[role]) && !isOllama(role);

export const runtime = signal({ ready: false, error: "", roles: {}, warnings: [], memory: null, pins: [] });

export const matchesModel = (name, model) => Boolean(name && model) && name.replace(/:latest$/, "") === model.replace(/:latest$/, "");

const resident = (ollama, name) =>
  (ollama ?? []).find((model) => matchesModel(model.name, name));

/** The per-role card model: default model, whether it is resident, and its memory. */
function roleState(role, defaults, health, memory, setup) {
  const meta = ROLE_META[role];
  const worker = (health?.workers ?? {})[role];
  const memoryWorker = (memory?.workers ?? {})[role];
  const model = defaults?.[role] ?? "—";
  // a pinned role is one the gateway is keeping resident on purpose, so its card stays on
  const pinned = (health?.pins ?? []).includes(role);
  // whether the default is on disk and its runtime accepts it; anything else means Setup, not a switch
  const setupEntry = setup?.roles?.[role];
  const setupState = setupEntry?.state ?? "unknown";
  const setupReason = setupEntry?.reason ?? "";
  // the decide engines Laya and GLiNER read text only, so the Decide page hides its image picker for them
  const readsImages = setupEntry?.reads_images !== false;
  // the whole model on disk (every part of a bundle), not what the process holds in RAM: a mmapped GGUF barely shows there
  const modelBytes = (setupEntry?.installed ?? []).find((entry) => entry.name === model)?.size_bytes ?? 0;

  if (setupEntry?.runner === "ollama") {
    // Ollama's models outlive the role: remember what it pulled, then match it.
    const running = memory?.loaded?.[role] ?? resident(memory?.ollama, model);
    // chat and embed share the machine's Ollama, so when it is not answering neither
    // can run: say that rather than leaving a card on a stale "not running" the person can only
    // fix from the server controls.
    const engine = health?.workers?.chat;
    const down = engine?.status === "unavailable";
    return {
      ...meta,
      runner: setupEntry.engine,
      role,
      model,
      loaded: !down && Boolean(running),
      pinned,
      runningModel: down ? null : running?.name ?? null,
      memoryBytes: down ? 0 : running?.size_bytes ?? 0,
      modelBytes,
      readsImages,
      status: down ? "unavailable" : running ? "loaded" : "idle",
      engineUrl: down ? engine?.url ?? "" : "",
      cpuPercent: 0,
      held: false,
      setupState,
      setupReason,
    };
  }

  // Worker roles report what they hold: the process can be up (ready) while its
  // model is still out of memory, so trust the worker's own `loaded` flag.
  const status = worker?.status ?? memoryWorker?.status ?? "stopped";
  const loaded = Boolean(worker?.loaded);
  return {
    ...meta,
    runner: setupEntry?.engine ?? "",
    role,
    model,
    loaded,
    pinned,
    runningModel: loaded ? (worker?.model || model) : null,
    memoryBytes: loaded ? (worker?.rss_bytes ?? memoryWorker?.rss_bytes ?? 0) : 0,
    modelBytes,
    readsImages,
    status,
    cpuPercent: memoryWorker?.cpu_percent ?? worker?.cpu_percent ?? 0,
    held: Boolean(memoryWorker?.held ?? worker?.held),
    setupState,
    setupReason,
  };
}

// `/admin/memory` reports Ollama's resident models but only under one flat list.
// A role's default is the best guess at which of them it is using, unless the
// model name is one another role already claimed.
function attributeOllama(memory, defaults) {
  const claimed = new Set();
  const loaded = {};
  for (const role of ROLE_ORDER) {
    if (!isOllama(role)) continue;
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
    const [healthResponse, memory, models, setup] = await Promise.all([
      getJson("/health"),
      getJson("/admin/memory").catch(() => null),
      getJson("/v1/models").catch(() => null),
      getJson("/admin/models/status").catch(() => null),
    ]);
    // One poll feeds both the sidebar cards and the gateway badge.
    health.value = healthResponse;
    healthError.value = "";
    if (setup?.roles) specs.value = setup.roles;
    const defaults = models?.defaults ?? {};
    const attributed = memory ? attributeOllama(memory, defaults) : null;
    const roles = {};
    for (const role of ROLE_ORDER) roles[role] = roleState(role, defaults, healthResponse, attributed, setup);
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

// One download per role, shared by every view of that role (sidebar card, Models page), so each
// shows the same progress: { model, busy, status, fraction, warnings, error }.
export const installs = signal({});

// Downloads the gateway reports, whatever started them: an install here, a load that downloads
// first, or a keep-warm pin. Polled, so a progress bar shows even when another tab or the server
// itself started the download. Same shape per role as `installs`.
export const downloads = signal({});

function updateInstall(role, patch) {
  installs.value = { ...installs.value, [role]: { ...installs.value[role], ...patch } };
}

function progressOf(event) {
  if (!event.total) return { status: event.status ?? "working…" };
  const completed = event.completed ?? 0;
  return { status: `${event.status ?? "downloading"} · ${gb(completed)} / ${gb(event.total)}`, fraction: completed / event.total };
}

function downloadProgressOf(entry) {
  if (!entry.total) return { role: entry.role, model: entry.model, busy: true, status: entry.status, fraction: 0, warnings: [], error: "" };
  const completed = entry.completed ?? 0;
  return {
    role: entry.role,
    model: entry.model,
    busy: true,
    status: `${entry.status} · ${gb(completed)} / ${gb(entry.total)}`,
    fraction: completed / entry.total,
    warnings: [],
    error: "",
  };
}

// Recent failures already shown once: a later success must not be overwritten by an old failure.
const surfacedFailures = new Set();

/** Poll the gateway for every download in flight, whatever started it. */
export async function pollDownloads() {
  try {
    const body = await getJson("/admin/models/downloads");
    const next = {};
    for (const entry of body.downloads ?? []) {
      if (entry.role) next[entry.role] = downloadProgressOf(entry);
    }
    downloads.value = next;
    // A download that failed while this browser was closed still reports: surface it like a
    // local failure, once, unless this tab already has its own (busier or newer) state for the role.
    for (const entry of body.recent ?? []) {
      if (!entry.error || entry.cancelled) continue;
      const failure = `${entry.model}@${entry.finished_at}`;
      if (surfacedFailures.has(failure)) continue;
      surfacedFailures.add(failure);
      if (installs.value[entry.role]?.busy || installs.value[entry.role]?.error) continue;
      installs.value = {
        ...installs.value,
        [entry.role]: {
          model: entry.model, busy: false, status: "install failed", fraction: 0,
          error: entry.error, code: entry.code ?? "", acceptUrl: entry.accept_url ?? "",
        },
      };
    }
  } catch {
    // keep the last good set: one transient failure must not flicker every progress bar
  }
  return downloads.value;
}

/** The download to show for `role`: this tab's own install first, else the gateway's report. */
export function downloadFor(role) {
  return installs.value[role]?.busy || installs.value[role]?.error
    ? installs.value[role]
    : downloads.value[role] ?? installs.value[role];
}

const WATCH_MS = 2000;
const WATCH_MISSES = 30;

/** After the pull stream drops, follow the download the server keeps running. Null on success, else the failure. */
async function watchDownload(role, model) {
  let misses = 0;
  while (misses < WATCH_MISSES) {
    await new Promise((resolve) => setTimeout(resolve, WATCH_MS));
    let body;
    try {
      body = await getJson("/admin/models/downloads");
    } catch {
      misses += 1;
      continue;
    }
    misses = 0;
    const active = (body.downloads ?? []).find((entry) => entry.model === model);
    if (active) {
      const { status, fraction } = downloadProgressOf(active);
      updateInstall(role, { status, fraction });
      continue;
    }
    const finished = (body.recent ?? []).find((entry) => entry.model === model);
    if (!finished) return { error: "The connection to the gateway dropped and the download is no longer running. Press download again to resume." };
    return finished.error ? finished : null;
  }
  return { error: "Lost contact with the gateway while downloading. Press download again to resume." };
}

/** Download `model` for `role`, publishing progress in `installs`. Resolves true once it is on disk. */
export async function install(role, model) {
  if (installs.value[role]?.busy) return false;
  installs.value = { ...installs.value, [role]: { role, model, busy: true, status: "starting…", fraction: 0, warnings: [], error: "", code: "", acceptUrl: "" } };
  try {
    const response = await pullModel({ role, model });
    for await (const event of ndjson(response)) {
      if (event.error) {
        const failure = new Error(event.error);
        failure.code = event.code ?? "";
        failure.acceptUrl = event.accept_url ?? "";
        failure.model = event.model ?? model;
        failure.cancelled = Boolean(event.cancelled);
        throw failure;
      }
      if (event.warning) updateInstall(role, { warnings: [...installs.value[role].warnings, event.warning] });
      else updateInstall(role, progressOf(event));
    }
    updateInstall(role, { busy: false, status: `installed ${model}`, fraction: 1, code: "", acceptUrl: "" });
    return true;
  } catch (caught) {
    let error = caught;
    if (caught instanceof TypeError) {
      // fetch reports a dropped connection as a TypeError; the server keeps downloading regardless
      const lost = await watchDownload(role, model);
      if (!lost) {
        updateInstall(role, { busy: false, status: `installed ${model}`, fraction: 1, code: "", acceptUrl: "" });
        return true;
      }
      error = Object.assign(new Error(lost.error), { code: lost.code, acceptUrl: lost.accept_url, cancelled: Boolean(lost.cancelled) });
    }
    if (error.cancelled) {
      updateInstall(role, { busy: false, status: "download stopped", fraction: 0, error: "", code: "", acceptUrl: "" });
      return false;
    }
    updateInstall(role, { busy: false, status: "install failed", fraction: 0, error: error.message, code: error.code ?? "", acceptUrl: error.acceptUrl ?? "" });
    return false;
  } finally {
    await pollRuntime();
  }
}

/** Stop the download for `role`, wherever it was started. Partial files stay, so a retry resumes. */
export async function stopDownload(role) {
  const model = downloadFor(role)?.model;
  if (!model) return;
  try {
    await stopPull({ role, model });
  } catch {
    // it finished or failed on its own just now: the next poll shows how
  }
  await pollDownloads();
}
