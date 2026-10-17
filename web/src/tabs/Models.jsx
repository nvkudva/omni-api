import { useEffect, useState } from "preact/hooks";
import { getJson, ndjson } from "../api/client.js";
import {
  checkModel,
  deleteModel,
  loadRole,
  memory as fetchMemory,
  modelStatus,
  pullModel,
  searchModels,
  serverInfo,
  setRoleDefault,
  unloadRole,
  pinRole,
  workerAction,
  workersAction,
} from "../api/endpoints.js";
import { ROLE_META, ROLE_ORDER, runtime } from "../api/roles.js";
import { gb } from "../lib/format.js";
import { usePolling } from "../hooks/usePolling.js";
import { ask } from "../components/Confirm.jsx";
import * as store from "../store.js";
import * as ui from "../components/ui.jsx";
import { Panel, Workbench } from "../components/workbench.jsx";
import styles from "./tabs.module.css";

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
  const run = async (action, done) => {
    setBusy(true);
    try {
      const result = await action();
      if (result) onChanged(done);
    } catch (error) {
      if (error.code !== "overload") {
        onChanged(error.message);
      } else if (await ask({
        title: "Load anyway?",
        message: "This model has a problem on this machine:",
        reasons: error.blockers,
        confirmLabel: "Load anyway",
        cancelLabel: "Not now",
      })) {
        try {
          const forced = await action({ force: true });
          if (forced) onChanged(done);
        } catch (forced) {
          onChanged(forced.message);
        }
      }
    } finally {
      setBusy(false);
    }
  };
  return [busy, run];
}

/** Keep-warm switch: on loads the default now and holds it, off unloads it. */
export function WarmSwitch({ role, model, pinned, disabled, onChanged }) {
  const [busy, setBusy] = useState(false);
  const flip = async () => {
    const next = !pinned;
    setBusy(true);
    try {
      await pinRole({ role, model, pinned: next });
      onChanged(next ? `keeping ${model} warm` : `stopped keeping ${model} warm`);
    } catch (error) {
      if (error.code !== "overload") {
        onChanged(error.message);
      } else if (await ask({
        title: `Keep ${ROLE_META[role]?.label ?? role} warm anyway?`,
        message: `${model} on this machine:`,
        reasons: error.blockers,
        advisories: runtime.value.warnings,
        confirmLabel: "Load anyway",
        cancelLabel: "Not now",
      })) {
        try {
          await pinRole({ role, model, pinned: next, force: true });
          onChanged(`keeping ${model} warm`);
        } catch (forced) {
          onChanged(forced.message);
        }
      }
    } finally {
      setBusy(false);
    }
  };
  return (
    <ui.Field label={pinned ? "Warm: on (stays loaded)" : "Warm: off (unloads when idle)"}>
      <ui.Button small disabled={busy || disabled} onClick={flip}>{pinned ? "Turn off" : "Keep warm"}</ui.Button>
    </ui.Field>
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
              <ui.Button small variant="danger" onClick={() => remove(model.name)}>Delete</ui.Button>
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
      <ui.Grid>
        <ui.Field label="Query" hint="blank for most downloaded">
          <input placeholder="e.g. kokoro" value={query} onInput={(event) => setQuery(event.currentTarget.value)} onKeyDown={(event) => event.key === "Enter" && search()} />
        </ui.Field>
      </ui.Grid>
      <ui.Row end>
        <ui.Button variant="primary" disabled={busy} onClick={search}>{busy ? "Searching…" : "Search"}</ui.Button>
      </ui.Row>
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

/** Download a model by id or page URL, after checking the role's runtime can run it. */
export function Install({ role, runner, initial, onInstalled }) {
  const [model, setModel] = useState(initial ?? "");
  const [check, setCheck] = useState(null);
  const [checking, setChecking] = useState(false);
  const [progress, setProgress] = useState(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (initial) {
      setModel(initial);
      setCheck(null);
      setProgress(null);
    }
  }, [initial]);

  const verify = async () => {
    const name = model.trim();
    if (!name) return;
    setChecking(true);
    try {
      const params = new URLSearchParams({ role, model: name });
      setCheck(await checkModel(params));
    } catch (failure) {
      setCheck({ supported: false, reason: failure.message, installed: false, size_bytes: null, blockers: [] });
    } finally {
      setChecking(false);
    }
  };

  const install = async () => {
    const name = model.trim();
    if (!name) return;
    setBusy(true);
    setProgress({ status: "starting…", fraction: 0 });
    try {
      const response = await pullModel({ role, model: name });
      const warnings = [];
      for await (const event of ndjson(response)) {
        if (event.error) throw new Error(event.error);
        if (event.warning) warnings.push(event.warning);
        const sizeText = event.total ? ` · ${gb(event.completed ?? 0)} / ${gb(event.total)}` : "";
        setProgress({ status: `${event.status ?? "working"}${sizeText}`, fraction: event.total ? (event.completed ?? 0) / event.total : 0, warnings });
      }
      setProgress({ status: `installed ${name} — pick it as the current model above`, fraction: 1, warnings });
      onInstalled();
    } catch (error) {
      setProgress({ status: "failed", fraction: 0, error: error.message });
    } finally {
      setBusy(false);
    }
  };

  const runnable = check && check.supported;
  return (
    <>
      <ui.Grid>
        <ui.Field label="Model" hint={HINTS[runner] ?? HINTS.other}>
          <input
            placeholder="repo id or https://huggingface.co/… page URL"
            value={model}
            onInput={(event) => { setModel(event.currentTarget.value); setCheck(null); }}
          />
        </ui.Field>
      </ui.Grid>
      {check && !check.supported && <ui.ErrorText>{check.reason}</ui.ErrorText>}
      {check && check.supported && (
        <ui.Meta>
          {check.installed ? "already downloaded" : "compatible"}
          {check.size_bytes ? ` · ${gb(check.size_bytes)}` : ""}
          {check.blockers?.length ? ` · ${check.blockers.join(" ")}` : ""}
        </ui.Meta>
      )}
      {progress && (
        <>
          <ui.Meter fraction={progress.fraction} />
          <ui.Meta>{progress.status}</ui.Meta>
          {progress.warnings?.length > 0 && <ui.Notice>{progress.warnings.join(" ")}</ui.Notice>}
          {progress.error && <ui.ErrorText>{progress.error}</ui.ErrorText>}
        </>
      )}
      <ui.Row end>
        <ui.Button disabled={checking || busy || !model.trim()} onClick={verify}>{checking ? "Checking…" : "Check"}</ui.Button>
        <ui.Button variant="primary" disabled={busy || !runnable || check?.installed} onClick={install}>
          {busy ? "Installing…" : "Install"}
        </ui.Button>
      </ui.Row>
    </>
  );
}

/** Everything one role needs: its current model, what is downloaded, and how to add more. */
export function RoleSetup({ role, entry, pinned, onChanged }) {
  const meta = ROLE_META[role];
  const names = entry.installed.map((model) => model.name);
  if (!names.includes(entry.default)) names.unshift(entry.default);
  const current = entry.installed.find((model) => model.name === entry.default);
  const [busy, run] = useAction(onChanged);
  const [pick, setPick] = useState(null);

  const load = () => run((options) => setResidency(role, true, entry.default, options), `loaded ${entry.default}`);
  const unload = () => run(() => setResidency(role, false, entry.default), `unloaded ${entry.default}`);

  const choose = async (model) => {
    try {
      const result = await setRoleDefault({ role, model });
      onChanged(result.warnings?.join(" ") ?? `now serving ${model}`);
    } catch (error) {
      onChanged(error.message);
    }
  };

  return (
    <Panel
      title={meta?.label ?? role}
      meta={`${entry.runner} · ${entry.installed.length} downloaded · ${gb(entry.total_bytes)} · ${STATE_LABEL[entry.state] ?? entry.state}`}
    >
      {entry.reason && <ui.ErrorText>{entry.reason}</ui.ErrorText>}
      <ui.Grid>
        <ui.Field label="Current model">
          <select class={styles.tableSelect} value={entry.default} onChange={(event) => choose(event.currentTarget.value)}>
            {names.map((name) => <option key={name} value={name}>{name}</option>)}
          </select>
        </ui.Field>
        <ui.Field label={current ? `On disk · ${modelDetail(current)}` : "On disk"}>
          <span class={styles.buttons}>
            <ui.Button small disabled={busy} onClick={load}>Load</ui.Button>
            <ui.Button small disabled={busy} onClick={unload}>Unload</ui.Button>
          </span>
        </ui.Field>
        <WarmSwitch role={role} model={entry.default} pinned={pinned} disabled={entry.state !== "ready"} onChanged={onChanged} />
      </ui.Grid>
      <h3 class={styles.subhead}>Downloaded</h3>
      <DownloadedList role={role} installed={entry.installed} onChanged={onChanged} />
      <h3 class={styles.subhead}>Find more</h3>
      <Search role={role} onPick={setPick} />
      <h3 class={styles.subhead}>Install</h3>
      <Install role={role} runner={entry.runner} initial={pick} onInstalled={() => onChanged("")} />
    </Panel>
  );
}

/** Start, stop and restart the workers and Ollama. Kept beside the models they serve. */
export function GlobalControls({ managed, onChanged }) {
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
    <Panel title="Workers" meta="every role's runtime at once">
      <ui.Row>
        <ui.Button small disabled={busy} onClick={() => run("restart", "Restart all")}>Restart all</ui.Button>
        <ui.Button small variant="danger" disabled={busy} onClick={() => run("stop", "Stop all", true)}>Stop all</ui.Button>
        <ui.Button small disabled={busy || !managed} onClick={restartOllama} title={managed ? "Restart the Ollama engine" : "Ollama is not managed by omni on this machine"}>Restart Ollama</ui.Button>
      </ui.Row>
    </Panel>
  );
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
  const role = roles.embed?.default === model.name ? "embed" : "chat";
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
  if (!memory) return <Panel title="Memory"><ui.Empty>Reading memory…</ui.Empty></Panel>;
  const used = memory.total_bytes - memory.available_bytes;
  const fraction = used / memory.total_bytes;
  const [load1, load5, load15] = memory.load_average.map((value) => value.toFixed(2));
  return (
    <Panel title="Memory" meta={`${gb(used)} of ${gb(memory.total_bytes)} used`}>
      <ui.Meter fraction={fraction} high={fraction > 0.85} />
      <ui.Meta>RAM {gb(memory.available_bytes)} free · swap {gb(memory.swap_used_bytes)} used · disk {gb(memory.disk_free_bytes)} free</ui.Meta>
      <ui.Meter fraction={memory.cpu_percent / 100} high={memory.cpu_percent > 90 || memory.load_average[0] > memory.cpu_count} />
      <ui.Meta>CPU {memory.cpu_percent.toFixed(0)}% · load {load1} / {load5} / {load15} on {memory.cpu_count} cores</ui.Meta>
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
    </Panel>
  );
}

export default function Models({ active }) {
  const [status, setStatus] = useState(null);
  const [managed, setManaged] = useState(false);
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
      setError("");
    } catch (failure) {
      setError(failure.message);
    }
    try {
      setPins((await getJson("/admin/pins")).pinned ?? []);
    } catch {
      setPins([]);
    }
  };

  useEffect(() => {
    if (active) {
      setFocus(store.get("omni.setupRole", null));
      refresh();
    }
  }, [active]);

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
      {!status && !error && <Panel title="Roles"><ui.Empty><ui.Spinner /> loading models…</ui.Empty></Panel>}
      {status && (
        <>
          <GlobalControls managed={managed} onChanged={changed} />
          {ordered.map(([role, entry]) => (
            <RoleSetup key={role} role={role} entry={entry} pinned={pins.includes(role)} onChanged={changed} />
          ))}
          <Memory memory={memory} roles={Object.fromEntries(entries.map(([role, entry]) => [role, { default: entry.default }]))} onChanged={changed} />
        </>
      )}
    </Workbench>
  );
}
