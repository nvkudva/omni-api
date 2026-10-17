import { useEffect, useState } from "preact/hooks";
import { getJson } from "../api/client.js";
import { models } from "../api/models.js";
import {
  checkModel,
  deleteModel,
  loadRole,
  memory as fetchMemory,
  modelStatus,
  saveSetting,
  searchModels,
  serverInfo,
  setRoleDefault,
  unloadRole,
  workerAction,
  workersAction,
} from "../api/endpoints.js";
import { OLLAMA_ROLES, ROLE_META, ROLE_ORDER, install, installs, matchesModel, setPinned } from "../api/roles.js";
import { health, pollHealth } from "../api/health.js";
import { gb } from "../lib/format.js";
import { usePolling } from "../hooks/usePolling.js";
import { ask, withForce } from "../components/Confirm.jsx";
import * as store from "../store.js";
import * as ui from "../components/ui.jsx";
import { Workbench } from "../components/workbench.jsx";
import styles from "./tabs.module.css";
import setup from "./Models.module.css";

const MEMORY_POLL_MS = 5000;
const HINTS = {
  ollama: "Ollama: a library name (qwen2.5vl:7b) or any GGUF repo on Hugging Face as hf.co/user/repo-GGUF:Q4_K_M.",
  other: "Hugging Face repo id or page URL, e.g. mlx-community/whisper-large-v3-turbo. Downloads to the HF cache.",
};

const STATE_LABEL = { ready: "ready", missing: "needs setup", incompatible: "needs setup", unknown: "checking…" };

function modelDetail(model) {
  const detail = [model.parameters, model.quantization].filter(Boolean).join(" · ");
  return `${gb(model.size_bytes)}${detail ? ` · ${detail}` : ""}`;
}

function setResidency(role, load, model, { force = false } = {}) {
  const call = load ? loadRole : unloadRole;
  return call({ role, model, force });
}

/**
 * Run an action, and if the gateway refuses because of something about this model, ask before
 * forcing it. `action` is called with `{ force: true }` on the retry.
 */
function useAction(onChanged) {
  const [busy, setBusy] = useState(false);
  const run = async (action, done, prompt = { title: "Load anyway?", message: "This model has a problem on this machine:" }) => {
    setBusy(true);
    try {
      const result = await withForce(action, prompt);
      if (result) onChanged(done);
    } catch (error) {
      onChanged(error.message);
    } finally {
      setBusy(false);
    }
  };
  return [busy, run];
}

/** A card that sizes to its content; the workbench Panel scrolls its body, which clips these. */
function Section({ title, meta, role, tools, children }) {
  return (
    <section class={setup.setup}>
      {(title || meta) && (
        <header class={setup.setupHead}>
          <div class={setup.setupTitleGroup}>
            {title && <h2 class={setup.setupTitle}>{title}</h2>}
            {meta && <span class={setup.setupMeta}>{meta}</span>}
          </div>
          <div class={setup.setupTools}>
            {tools}
            {role && (
              <a class={setup.logLink} href={`/admin/logs/${role}`} target="_blank" rel="noreferrer" title="Open this role's log file">Log</a>
            )}
          </div>
        </header>
      )}
      <div class={setup.setupBody}>{children}</div>
    </section>
  );
}

/** One saved default a role's workers read at start; saving restarts only the workers that use it. */
function Tunable({ spec, stored, onSaved }) {
  const [draft, setDraft] = useState(String(stored));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState("");

  useEffect(() => { setDraft(String(stored)); }, [stored]);

  const dirty = draft !== String(stored);
  const save = async () => {
    const [section, key] = spec.key.split(".");
    const value = spec.number ? Number(draft) : draft;
    setBusy(true);
    setError("");
    setSaved("");
    try {
      const result = await saveSetting({ section, key, value });
      setSaved(result.restarted.length ? `saved; restarted ${result.restarted.join(", ")}` : "saved");
      await onSaved();
    } catch (failure) {
      setError(failure.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <ui.Field label={spec.label} hint={error || saved || spec.hint}>
      <div class={setup.tunable}>
        <input
          type={spec.number ? "number" : "text"}
          step={spec.step}
          value={draft}
          onInput={(event) => setDraft(event.currentTarget.value)}
          onKeyDown={(event) => { if (event.key === "Enter" && dirty && !busy) save(); }}
        />
        <ui.Button small variant="primary" disabled={!dirty || busy || draft === ""} onClick={save}>Save</ui.Button>
      </div>
    </ui.Field>
  );
}

const ROLE_TUNABLES = {
  tts: [
    { key: "tts.voice", label: "Default voice", hint: "Voice used when a speech request names none. Restarts tts.", number: false },
  ],
  image: [
    { key: "image.steps", label: "Image steps", hint: "Denoising steps per image. More is slower and finer. Restarts image.", number: true, step: 1 },
  ],
  video: [
    { key: "video.frames", label: "Video frames", hint: "Frames per clip. Restarts video.", number: true, step: 1 },
    { key: "video.fps", label: "Video fps", hint: "Playback rate of generated clips. Restarts video.", number: true, step: 1 },
  ],
};

const GATEWAY_TUNABLE = {
  key: "gateway.idle_unload_minutes",
  label: "Idle unload (minutes)",
  hint: "Unload a worker's model after this long unused. Restarts every worker.",
  number: true,
  step: "any",
};

/** Keep-warm switch: on loads the default now and holds it, off unloads it. */
export function WarmSwitch({ role, model, pinned, disabled, onChanged }) {
  const [busy, run] = useAction(onChanged);
  const flip = () => run(
    (options) => setPinned(role, !pinned, options),
    pinned ? `stopped keeping ${model} warm` : `keeping ${model} warm`,
    { title: `Keep ${ROLE_META[role]?.label ?? role} warm anyway?`, message: `${model} on this machine:` },
  );
  return (
    <ui.Button
      small
      disabled={busy || disabled}
      onClick={flip}
      title={pinned ? "Warm: on (stays loaded)" : "Warm: off (unloads when idle)"}
    >
      {pinned ? "Warm: off" : "Keep warm"}
    </ui.Button>
  );
}

/** Every downloaded model for one role, with its size and a way to delete it. */
export function DownloadedList({ role, installed, onChanged }) {
  const remove = async (model) => {
    const go = await ask({
      title: `Delete ${model}?`,
      message: "This frees its disk space. Installing it again downloads it all over.",
      confirmLabel: "Delete",
      tone: "danger",
    });
    if (!go) return;
    try {
      await deleteModel({ role, model });
      onChanged(`deleted ${model}`);
    } catch (error) {
      onChanged(error.message);
    }
  };
  if (!installed.length) return <ui.Empty>Nothing downloaded for this role yet.</ui.Empty>;
  return (
    <table>
      <thead><tr><th>Model</th><th>Size</th><th /></tr></thead>
      <tbody>
        {installed.map((model) => (
          <tr key={model.name}>
            <td class={styles.mono}>{model.name}</td>
            <td class={styles.numeric}>{modelDetail(model)}</td>
            <td class={styles.actionCell}>
              <ui.IconButton icon="trash" danger label={`Delete ${model.name}`} title="Delete (frees disk space)" onClick={() => remove(model.name)} />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** Find a model on Hugging Face that this role's runtime can load. */
export function Search({ role, onPick }) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const search = async () => {
    setBusy(true);
    setError("");
    try {
      const params = new URLSearchParams({ role, q: query.trim() });
      setResults((await searchModels(params)).results);
    } catch (failure) {
      setError(failure.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div class={setup.line}>
        <input
          placeholder="Search Hugging Face (blank: most downloaded)"
          value={query}
          onInput={(event) => setQuery(event.currentTarget.value)}
          onKeyDown={(event) => event.key === "Enter" && search()}
        />
        <ui.Button small variant="primary" disabled={busy} onClick={search}>{busy ? "Searching…" : "Search"}</ui.Button>
      </div>
      {error && <ui.ErrorText>{error}</ui.ErrorText>}
      {results && (results.length
        ? (
          <table>
            <thead><tr><th>Model</th><th>Downloads</th><th>Likes</th><th /></tr></thead>
            <tbody>
              {results.map((result) => (
                <tr key={result.id}>
                  <td class={styles.mono}><a class={styles.link} href={`https://huggingface.co/${result.id}`} target="_blank" rel="noreferrer">{result.id}</a></td>
                  <td class={styles.numeric}>{result.downloads.toLocaleString()}</td>
                  <td class={styles.numeric}>{result.likes.toLocaleString()}</td>
                  <td class={styles.actionCell}><ui.Button small onClick={() => onPick(result.name)}>Select</ui.Button></td>
                </tr>
              ))}
            </tbody>
          </table>
        )
        : <ui.Empty>No supported models match.</ui.Empty>)}
    </>
  );
}

/** A gated repo: accept its terms on Hugging Face, then retry the same download. */
export function GatedNotice({ model, acceptUrl, busy, onRetry, actionLabel = "Retry install", busyLabel = "Installing…" }) {
  const url = acceptUrl || `https://huggingface.co/${model}`;
  return (
    <>
      <ui.Notice tone="error">
        {model} needs its terms accepted before it can download.{" "}
        <a href={url} target="_blank" rel="noreferrer">Accept terms on Hugging Face</a>
        {" "}— sign in there, accept, then retry here. A Hugging Face token with access may also be required.
      </ui.Notice>
      <ui.Row>
        <ui.Button small variant="primary" disabled={busy} onClick={onRetry}>{busy ? busyLabel : actionLabel}</ui.Button>
      </ui.Row>
    </>
  );
}

/** Where a role's download stands: the same progress the sidebar card shows. */
function InstallProgress({ role }) {
  const progress = installs.value[role];
  if (!progress) return null;
  if (progress.code === "gated") {
    return (
      <>
        <ui.Meter fraction={progress.fraction} />
        <ui.Meta>{progress.status}</ui.Meta>
        <GatedNotice model={progress.model} acceptUrl={progress.acceptUrl} busy={progress.busy} onRetry={() => install(role, progress.model)} />
      </>
    );
  }
  return (
    <>
      <ui.Meter fraction={progress.fraction} />
      <ui.Meta>{progress.status}</ui.Meta>
      {progress.warnings?.length > 0 && <ui.Notice>{progress.warnings.join(" ")}</ui.Notice>}
      {progress.error && <ui.ErrorText>{progress.error}</ui.ErrorText>}
    </>
  );
}

/** Download a model by id or page URL, after checking the role's runtime can run it. */
export function Install({ role, runner, initial, onInstalled }) {
  const [model, setModel] = useState(initial ?? "");
  const [check, setCheck] = useState(null);
  const [checking, setChecking] = useState(false);
  const busy = Boolean(installs.value[role]?.busy);

  useEffect(() => {
    if (initial) {
      setModel(initial);
      setCheck(null);
    }
  }, [initial]);

  const verify = async () => {
    const name = model.trim();
    if (!name) return null;
    setChecking(true);
    let result;
    try {
      result = await checkModel(new URLSearchParams({ role, model: name }));
    } catch (failure) {
      result = { supported: false, reason: failure.message, installed: false, size_bytes: null, blockers: [] };
    } finally {
      setChecking(false);
    }
    setCheck(result);
    return result;
  };

  // Install checks first when nobody has, so one click is enough.
  const download = async () => {
    const verdict = check ?? (await verify());
    if (!verdict?.supported || verdict.installed) return;
    if (await install(role, model.trim())) {
      setCheck({ ...verdict, installed: true });
      onInstalled();
    }
  };

  return (
    <>
      <div class={setup.line}>
        <input
          placeholder="repo id or huggingface.co page URL"
          title={HINTS[runner] ?? HINTS.other}
          value={model}
          onInput={(event) => { setModel(event.currentTarget.value); setCheck(null); }}
        />
        <ui.Button small disabled={checking || busy || !model.trim()} onClick={verify}>{checking ? "Checking…" : "Check"}</ui.Button>
        <ui.Button
          small
          variant="primary"
          disabled={checking || busy || !model.trim() || check?.supported === false || check?.installed}
          onClick={download}
        >
          {busy ? "Installing…" : "Install"}
        </ui.Button>
      </div>
      {check && !check.supported && <ui.ErrorText>{check.reason}</ui.ErrorText>}
      {check?.gated && installs.value[role]?.code !== "gated" && (
        <GatedNotice model={check.model} acceptUrl={check.accept_url} busy={busy} onRetry={download} />
      )}
      {check && check.supported && !check.gated && (
        <ui.Meta>
          {check.installed ? "already downloaded" : "compatible"}
          {check.size_bytes ? ` · ${gb(check.size_bytes)}` : ""}
          {check.blockers?.length ? ` · ${check.blockers.join(" ")}` : ""}
        </ui.Meta>
      )}
    </>
  );
}

/** What /health knows about one role's runtime, behind its card's health button. */
function RoleHealth({ role }) {
  const worker = health.value?.workers?.[role];
  if (!worker) {
    return (
      <ui.Meta>
        {OLLAMA_ROLES.has(role)
          ? "Served by Ollama — see the Ollama card under Server."
          : "No health data yet."}
      </ui.Meta>
    );
  }
  return (
    <dl class={setup.health}>
      <div><dt>Status</dt><dd><ui.Dot status={worker.status} />{worker.status}{worker.held ? " (held)" : ""}</dd></div>
      <div><dt>Loaded</dt><dd>{worker.loaded ? (worker.model || "loaded") : "—"}</dd></div>
      <div><dt>Process</dt><dd>{worker.pid ?? worker.url ?? "—"}</dd></div>
      <div><dt>Restarts</dt><dd>{worker.restarts ?? "—"}</dd></div>
    </dl>
  );
}

/** Everything one role needs: its current model, what is downloaded, and how to add more. */
export function RoleSetup({ role, entry, pinned, tunables, onChanged, onSaved }) {
  const meta = ROLE_META[role];
  const names = entry.installed.map((model) => model.name);
  if (!names.includes(entry.default)) names.unshift(entry.default);
  const current = entry.installed.find((model) => model.name === entry.default);
  const [busy, run] = useAction(onChanged);
  const [pick, setPick] = useState(null);
  const [showHealth, setShowHealth] = useState(false);
  const specs = (tunables && ROLE_TUNABLES[role]) || [];

  const load = () => run((options) => setResidency(role, true, entry.default, options), `loaded ${entry.default}`);
  const unload = () => run(() => setResidency(role, false, entry.default), `unloaded ${entry.default}`);
  const installing = Boolean(installs.value[role]?.busy);
  const installDefault = async () => {
    if (await install(role, entry.default)) onChanged(`installed ${entry.default}`);
  };

  const choose = async (model) => {
    try {
      const result = await setRoleDefault({ role, model });
      onChanged(result.warnings?.join(" ") ?? `now serving ${model}`);
    } catch (error) {
      onChanged(error.message);
    }
  };

  const healthLabel = `${meta?.label ?? role} health details`;
  return (
    <Section
      title={meta?.label ?? role}
      role={role}
      meta={`${entry.runner} · ${entry.installed.length} downloaded · ${gb(entry.total_bytes)} · ${STATE_LABEL[entry.state] ?? entry.state}`}
      tools={(
        <ui.IconButton
          icon="health"
          label={healthLabel}
          aria-expanded={showHealth}
          onClick={() => setShowHealth(!showHealth)}
        />
      )}
    >
      {showHealth && <RoleHealth role={role} />}
      {entry.reason && <ui.Notice tone={entry.state === "missing" ? "warn" : "error"}>{entry.reason}</ui.Notice>}
      {entry.state === "missing" && (
        <ui.Row>
          <ui.Button small variant="primary" disabled={installing} onClick={installDefault}>
            {installing ? "Installing…" : `Install ${entry.default}`}
          </ui.Button>
        </ui.Row>
      )}
      <InstallProgress role={role} />
      <div class={setup.line} title={current ? `On disk · ${modelDetail(current)}` : "Not on disk"}>
        <select aria-label="Current model" value={entry.default} onChange={(event) => choose(event.currentTarget.value)}>
          {names.map((name) => <option key={name} value={name}>{name}</option>)}
        </select>
        <ui.Button small disabled={busy || entry.state !== "ready"} onClick={load}>Load</ui.Button>
        <ui.Button small disabled={busy} onClick={unload}>Unload</ui.Button>
        <WarmSwitch role={role} model={entry.default} pinned={pinned} disabled={entry.state !== "ready"} onChanged={onChanged} />
      </div>
      <DownloadedList role={role} installed={entry.installed} onChanged={onChanged} />
      {specs.map((spec) => <Tunable key={spec.key} spec={spec} stored={tunables[spec.key]} onSaved={onSaved} />)}
      <details class={setup.more} open={Boolean(pick)}>
        <summary>Find &amp; install a model</summary>
        <Search role={role} onPick={setPick} />
        <Install role={role} runner={entry.runner} initial={pick} onInstalled={() => onChanged("")} />
      </details>
    </Section>
  );
}

/** Start, stop and restart the workers and Ollama. Kept beside the models they serve. */
export function GlobalControls({ managed, tunables, onChanged, onSaved }) {
  const [busy, setBusy] = useState(false);

  const run = async (action, label, danger) => {
    if (danger && !(await ask({
      title: `${label}?`,
      message: "In-flight requests to every worker will be dropped.",
      confirmLabel: label,
      tone: "danger",
    }))) return;
    setBusy(true);
    try {
      await workersAction(action);
      onChanged(`all workers: ${action} sent`);
    } catch (error) {
      onChanged(error.message);
    } finally {
      setBusy(false);
    }
  };

  const restartOllama = async () => {
    setBusy(true);
    try {
      await workerAction("chat", "restart");
      onChanged("ollama: restart sent");
    } catch (error) {
      onChanged(error.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Section title="Workers" meta="every role's runtime at once">
      <ui.Row>
        <ui.Button small disabled={busy} onClick={() => run("restart", "Restart all")}>Restart all</ui.Button>
        <ui.Button small variant="danger" disabled={busy} onClick={() => run("stop", "Stop all", true)}>Stop all</ui.Button>
        <ui.Button small disabled={busy || !managed} onClick={restartOllama} title={managed ? "Restart the Ollama engine" : "Ollama is not managed by omni on this machine"}>Restart Ollama</ui.Button>
      </ui.Row>
      {tunables && <Tunable spec={GATEWAY_TUNABLE} stored={tunables[GATEWAY_TUNABLE.key]} onSaved={onSaved} />}
    </Section>
  );
}

/** The Health page's old tables, kept for tests: one row per worker and per model. */
export function WorkerRows({ workers }) {
  return Object.entries(workers).map(([name, worker]) => (
    <tr key={name}>
      <td><span class={styles.nameCell}><ui.Dot status={worker.status} />{name}</span></td>
      <td>{worker.status}{worker.held ? " (held)" : ""}</td>
      <td class={styles.mono}>{worker.loaded ? (worker.model || "loaded") : "—"}</td>
      <td class={styles.mono}>{worker.pid ?? worker.url ?? "—"}</td>
      <td class={styles.numeric}>{worker.restarts ?? "—"}</td>
    </tr>
  ));
}

export function ModelRows() {
  const { data, defaults } = models.value;
  return data.map((model) => (
    <tr key={`${model.role}:${model.id}`}>
      <td>{model.role}</td>
      <td class={styles.mono}>{model.id}{defaults[model.role] === model.id ? "  (default)" : ""}</td>
    </tr>
  ));
}

function useMemory(active) {
  const [memory, setMemory] = useState(null);
  usePolling(async () => {
    try {
      setMemory(await fetchMemory());
    } catch {
      setMemory(null);
    }
  }, MEMORY_POLL_MS, { active });
  return memory;
}

export function OllamaRow({ model, roles, onChanged }) {
  const [busy, run] = useAction(onChanged);
  const role = Object.entries(roles).find(([, entry]) => matchesModel(model.name, entry.default))?.[0] ?? "chat";
  return (
    <tr>
      <td class={styles.mono}>{model.name}</td>
      <td class={styles.numeric}>{gb(model.size_bytes)}</td>
      <td class={styles.numeric}>{gb(model.vram_bytes)}</td>
      <td class={styles.actionCell}><ui.Button small disabled={busy} onClick={() => run(() => setResidency(role, false, model.name), `unloaded ${model.name}`)}>Unload</ui.Button></td>
    </tr>
  );
}

export function WorkerRow({ name, worker, onChanged }) {
  const [busy, run] = useAction(onChanged);
  const control = (action) => run(() => workerAction(name, action), `${name}: ${action} sent`);
  const stopped = worker.status === "stopped";
  return (
    <tr>
      <td><span class={styles.nameCell}><ui.Dot status={worker.status} />{name}</span></td>
      <td class={styles.numeric}>{worker.status}</td>
      <td class={styles.numeric}>{worker.cpu_percent.toFixed(0)}%</td>
      <td class={styles.numeric}>{gb(worker.rss_bytes)}</td>
      <td class={styles.actionCell}>
        <span class={styles.buttons}>
          {stopped
            ? <ui.Button small disabled={busy} onClick={() => control("start")}>Start</ui.Button>
            : <ui.Button small variant="danger" disabled={busy} onClick={() => control("stop")}>Stop</ui.Button>}
          <ui.Button small disabled={busy} onClick={() => control("restart")}>Restart</ui.Button>
        </span>
      </td>
    </tr>
  );
}

export function Memory({ memory, roles, onChanged }) {
  if (!memory) return <Section title="Memory"><ui.Empty>Reading memory…</ui.Empty></Section>;
  const used = memory.total_bytes - memory.available_bytes;
  const fraction = used / memory.total_bytes;
  const [load1, load5, load15] = memory.load_average.map((value) => value.toFixed(2));
  return (
    <Section title="Memory" meta={`${gb(used)} of ${gb(memory.total_bytes)} used`}>
      <div class={setup.meters}>
        <div>
          <ui.Meter fraction={fraction} high={fraction > 0.85} />
          <ui.Meta>RAM {gb(memory.available_bytes)} free · swap {gb(memory.swap_used_bytes)} used · disk {gb(memory.disk_free_bytes)} free</ui.Meta>
        </div>
        <div>
          <ui.Meter fraction={memory.cpu_percent / 100} high={memory.cpu_percent > 90 || memory.load_average[0] > memory.cpu_count} />
          <ui.Meta>CPU {memory.cpu_percent.toFixed(0)}% · load {load1} / {load5} / {load15} on {memory.cpu_count} cores</ui.Meta>
        </div>
      </div>
      {memory.warnings.map((warning, index) => <ui.Notice key={index}>{warning}</ui.Notice>)}
      <h3 class={styles.subhead}>Loaded in Ollama</h3>
      {memory.ollama.length
        ? (
          <table>
            <thead><tr><th>Model</th><th>Size</th><th>On GPU</th><th /></tr></thead>
            <tbody>
              {memory.ollama.map((model) => <OllamaRow key={model.name} model={model} roles={roles} onChanged={onChanged} />)}
            </tbody>
          </table>
        )
        : <ui.Meta>none loaded</ui.Meta>}
      <h3 class={styles.subhead}>Workers</h3>
      <table>
        <thead><tr><th>Worker</th><th>Status</th><th>CPU</th><th>Memory</th><th /></tr></thead>
        <tbody>
          {Object.entries(memory.workers).map(([name, worker]) => <WorkerRow key={name} name={name} worker={worker} onChanged={onChanged} />)}
        </tbody>
      </table>
    </Section>
  );
}

export default function Models({ active }) {
  const [status, setStatus] = useState(null);
  const [managed, setManaged] = useState(false);
  const [tunables, setTunables] = useState(null);
  const [pins, setPins] = useState([]);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [focus, setFocus] = useState(() => store.get("omni.setupRole", null));
  const memory = useMemory(active);

  const refresh = async () => {
    try {
      const [setup, info] = await Promise.all([modelStatus(), serverInfo().catch(() => null)]);
      setStatus(setup);
      setManaged(Boolean(info?.ollama?.managed));
      setTunables(info?.tunables ?? null);
      setError("");
    } catch (failure) {
      setError(failure.message);
    }
    pollHealth();
    try {
      setPins((await getJson("/admin/pins")).pinned ?? []);
    } catch {
      setPins([]);
    }
  };

  // Settings mounts this page only for its section, so mounting means showing:
  // load on mount, whether or not the caller passes `active`.
  useEffect(() => {
    setFocus(store.get("omni.setupRole", null));
  }, []);

  // A download started anywhere (the sidebar card's "Install now" too) changes what is on disk:
  // load on mount and again whenever the last running download ends.
  const isDownloading = Object.values(installs.value).some((progress) => progress.busy);
  useEffect(() => {
    if (!isDownloading) refresh();
  }, [isDownloading]);

  const changed = (message) => {
    setNotice(message);
    refresh();
  };

  const entries = status ? Object.entries(status.roles) : [];
  const ordered = focus && status?.roles[focus]
    ? [[focus, status.roles[focus]], ...entries.filter(([role]) => role !== focus)]
    : entries;
  const downloadedTotal = entries.reduce((total, [, entry]) => total + (entry.total_bytes ?? 0), 0);
  const ramUsed = memory ? memory.total_bytes - memory.available_bytes : null;

  const readout = [
    { label: "Roles", value: status ? entries.length : "—" },
    { label: "Downloaded", value: status ? gb(downloadedTotal) : "—" },
    { label: "RAM", value: memory ? `${gb(ramUsed)} / ${gb(memory.total_bytes)}` : "—" },
    { label: "Disk free", value: memory ? gb(memory.disk_free_bytes) : "—" },
  ];

  return (
    <Workbench single title="Models" subtitle="Each role's current model, what is downloaded for it, and how to add more." readout={readout}>
      {error && <ui.ErrorText>{error}</ui.ErrorText>}
      {notice && <ui.Notice>{notice}</ui.Notice>}
      {!status && !error && <Section title="Roles"><ui.Empty><ui.Spinner /> loading models…</ui.Empty></Section>}
      {status && (
        <>
          <GlobalControls managed={managed} tunables={tunables} onChanged={changed} onSaved={refresh} />
          <div class={setup.cards}>
            {ordered.map(([role, entry]) => (
              <RoleSetup key={role} role={role} entry={entry} pinned={pins.includes(role)} tunables={tunables} onChanged={changed} onSaved={refresh} />
            ))}
          </div>
          <Memory memory={memory} roles={Object.fromEntries(entries.map(([role, entry]) => [role, { default: entry.default }]))} onChanged={changed} />
        </>
      )}
    </Workbench>
  );
}
