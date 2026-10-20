import { useEffect, useState } from "preact/hooks";
import { getJson } from "../api/client.js";
import { models } from "../api/models.js";
import {
  loadRole,
  modelStatus,
  serverInfo,
  unloadRole,
  workerAction,
  workersAction,
} from "../api/endpoints.js";
import { downloads, installs, matchesModel, ROLE_META, ROLE_ORDER, runtime } from "../api/roles.js";
import { pollHealth } from "../api/health.js";
import { gb } from "../lib/format.js";
import { ask, withForce } from "../components/Confirm.jsx";
import * as ui from "../components/ui.jsx";
import { Workbench } from "../components/workbench.jsx";
import { RoleModelCard, Section, Tunable } from "../components/RoleModelCard.jsx";
import { FindAndInstall } from "../components/FindAndInstall.jsx";
import styles from "./tabs.module.css";
import setup from "./Models.module.css";

export { DownloadedList, WarmSwitch } from "../components/RoleModelCard.jsx";
export { GatedNotice, Install, Search } from "../components/FindAndInstall.jsx";
export { RoleModelCard as RoleSetup } from "../components/RoleModelCard.jsx";

const GATEWAY_TUNABLE = {
  key: "gateway.idle_unload_minutes",
  label: "Idle unload",
  hint: "Unload a worker's model after this long unused. Restarts every worker.",
  number: true,
  options: [
    { value: 0, label: "Never" },
    { value: 10, label: "10 minutes" },
    { value: 20, label: "20 minutes" },
  ],
};

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

function setResidency(role, load, model, { force = false } = {}) {
  const call = load ? loadRole : unloadRole;
  return call({ role, model, force });
}

const rankRole = (role) => {
  const index = ROLE_ORDER.indexOf(role);
  return index === -1 ? ROLE_ORDER.length : index;
};

/** The role cards in the same order as the mini cards' tabs, whatever order the
 *  status API happened to return its roles in. */
export function orderRoles(roles) {
  return Object.entries(roles).sort(([a], [b]) => rankRole(a) - rankRole(b));
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
        <ui.Button small disabled={busy} onClick={() => run("stop", "Stop all", true)}>Stop all</ui.Button>
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
      <td>{worker.loaded ? (worker.model || "loaded") : "—"}</td>
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
      <td>{model.id}{defaults[model.role] === model.id ? "  (default)" : ""}</td>
    </tr>
  ));
}

export function OllamaRow({ model, roles, onChanged }) {
  const [busy, run] = useAction(onChanged);
  const role = Object.entries(roles).find(([, entry]) => matchesModel(model.name, entry.default))?.[0] ?? "chat";
  return (
    <tr>
      <td>{model.name}</td>
      <td class={styles.numeric}>{gb(model.size_bytes)}</td>
      <td class={styles.numeric}>{gb(model.vram_bytes)}</td>
      <td class={styles.actionCell}><ui.Button small disabled={busy} onClick={() => run(() => setResidency(role, false, model.name), `unloaded ${model.name}`)}>Unload</ui.Button></td>
    </tr>
  );
}

export function WorkerRow({ name, worker, model, onChanged }) {
  const [busy, run] = useAction(onChanged);
  const label = ROLE_META[name]?.label ?? name;
  const control = (action) => run(() => workerAction(name, action), `${name}: ${action} sent`);
  const stop = async () => {
    const confirmed = await ask({
      title: `Stop ${label}?`,
      message: `In-flight ${label} requests will be interrupted.`,
      confirmLabel: "Stop",
      tone: "danger",
    });
    if (confirmed) control("stop");
  };
  const stopped = worker.status === "stopped";
  return (
    <tr>
      <td>{label}</td>
      <td>{worker.model || model || "—"}</td>
      <td><span class={styles.nameCell}><ui.Dot status={worker.status} />{worker.status}</span></td>
      <td class={styles.numeric}>{worker.cpu_percent.toFixed(0)}%</td>
      <td class={styles.numeric}>{gb(worker.rss_bytes)}</td>
      <td class={styles.actionCell}>
        <span class={styles.buttons}>
          {stopped
            ? <ui.Button small disabled={busy} onClick={() => control("start")}>Start</ui.Button>
            : <ui.Button small disabled={busy} onClick={stop}>Stop</ui.Button>}
          <ui.Button small disabled={busy} onClick={() => control("restart")}>Restart</ui.Button>
        </span>
      </td>
    </tr>
  );
}

// a worker with no model loaded still holds ~0.1 GB; unloading it frees nothing worth suggesting
const RECLAIMABLE_MIN_BYTES = 512 * 1024 * 1024;

/** What is holding memory and could be freed, largest first. */
function reclaimable(memory) {
  const items = memory.ollama.map((model) => ({ label: model.name, bytes: model.size_bytes ?? 0 }));
  for (const [name, worker] of Object.entries(memory.workers ?? {})) {
    if (worker.status !== "stopped" && worker.rss_bytes > 0) {
      items.push({ label: ROLE_META[name]?.label ?? name, bytes: worker.rss_bytes });
    }
  }
  return items
    .filter((item) => item.bytes >= RECLAIMABLE_MIN_BYTES)
    .sort((a, b) => b.bytes - a.bytes)
    .slice(0, 3)
    .map((item) => `${item.label} (${gb(item.bytes)})`)
    .join(", ");
}

export function Memory({ memory, roles, onChanged }) {
  if (!memory) return <Section title="Memory"><ui.Empty>Reading memory…</ui.Empty></Section>;
  const used = memory.total_bytes - memory.available_bytes;
  const fraction = used / memory.total_bytes;
  const pinned = (runtime.value.pins ?? []).length > 0;
  const largest = reclaimable(memory);
  const advise = (warning) => (/swap/i.test(warning) && largest
    ? `${warning}. Unload${pinned ? " or unpin" : ""}: ${largest}`
    : warning);
  const [load1, load5, load15] = (memory.load_average ?? [0, 0, 0]).map((value) => (value ?? 0).toFixed(2));
  return (
    <Section title="Memory" meta={`${gb(used)} of ${gb(memory.total_bytes)} used`}>
      <div class={setup.meters}>
        <div>
          <div class={setup.meterRow}>
            <span class={setup.meterLabel}>RAM</span>
            <ui.Meter fraction={fraction} high={fraction > 0.85} />
          </div>
          <ui.Meta>RAM {gb(memory.available_bytes)} free · swap {gb(memory.swap_used_bytes)} used · disk {gb(memory.disk_free_bytes)} free</ui.Meta>
        </div>
        <div>
          <div class={setup.meterRow}>
            <span class={setup.meterLabel}>CPU</span>
            <ui.Meter fraction={memory.cpu_percent / 100} high={memory.cpu_percent > 90 || memory.load_average[0] > memory.cpu_count} />
          </div>
          <ui.Meta>CPU {(memory.cpu_percent ?? 0).toFixed(0)}% · load {load1} / {load5} / {load15} on {memory.cpu_count ?? "?"} cores</ui.Meta>
        </div>
      </div>
      {(memory.warnings ?? []).filter(Boolean).map((warning, index) => <ui.Notice key={index}>{advise(warning)}</ui.Notice>)}
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
        <thead><tr><th>Worker</th><th>Model</th><th>Status</th><th>CPU</th><th>Memory</th><th /></tr></thead>
        <tbody>
          {Object.entries(memory.workers).map(([name, worker]) => <WorkerRow key={name} name={name} worker={worker} model={roles?.[name]?.default} onChanged={onChanged} />)}
        </tbody>
      </table>
    </Section>
  );
}

export default function Models({ role: filterRole = null, onRole }) {
  const [status, setStatus] = useState(null);
  const [managed, setManaged] = useState(false);
  const [tunables, setTunables] = useState(null);
  const [pins, setPins] = useState([]);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [addRole, setAddRole] = useState(null);
  // RAM figures ride the shared runtime poll; the meters themselves live on the Server tab.
  const memory = runtime.value.memory;

  const refresh = async () => {
    try {
      const [setupStatus, info] = await Promise.all([modelStatus(), serverInfo().catch(() => null)]);
      setStatus(setupStatus);
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

  const isDownloading = Object.values(installs.value).some((progress) => progress.busy)
    || Object.keys(downloads.value).length > 0;
  useEffect(() => {
    if (!isDownloading) refresh();
  }, [isDownloading]);

  const changed = (message) => {
    setNotice(message);
    refresh();
  };

  // Cards read in the mini tabs' order, not the API's; the picked role is
  // highlighted in the nav rather than pulled to the front.
  const allEntries = status ? orderRoles(status.roles) : [];
  const entries = filterRole ? allEntries.filter(([role]) => role === filterRole) : allEntries;
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
          {onRole && (
            <ui.Segmented
              label="Show role"
              value={filterRole ?? "all"}
              options={[{ value: "all", label: "All" }, ...allEntries.map(([role]) => ({ value: role, label: ROLE_META[role]?.label ?? role }))]}
              onChange={(value) => onRole(value === "all" ? null : value)}
            />
          )}
          <GlobalControls managed={managed} tunables={tunables} onChanged={changed} onSaved={refresh} />
          <div class={setup.cards}>
            {entries.map(([role, entry]) => (
              <RoleModelCard key={role} density="detail" role={role} entry={entry} pinned={pins.includes(role)} tunables={tunables} onChanged={changed} onSaved={refresh} onAdd={setAddRole} />
            ))}
          </div>
        </>
      )}
      {addRole && <FindAndInstall key={addRole} role={addRole} onClose={() => setAddRole(null)} onInstalled={() => changed("")} />}
    </Workbench>
  );
}
