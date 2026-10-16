import { useEffect, useState } from "preact/hooks";
import { ndjson } from "../api/client.js";
import {
  adminModels,
  deleteModel,
  loadRole,
  memory as fetchMemory,
  pullModel,
  searchModels,
  setRoleDefault,
  unloadRole,
  workerAction,
} from "../api/endpoints.js";
import { loadModels } from "../api/models.js";
import { gb } from "../lib/format.js";
import { usePolling } from "../hooks/usePolling.js";
import * as ui from "../components/ui.jsx";
import { Panel, Workbench } from "../components/workbench.jsx";
import styles from "./tabs.module.css";

const MEMORY_POLL_MS = 5000;
const HINTS = {
  ollama: "Ollama: a library name (qwen2.5vl:7b) or any GGUF repo on Hugging Face as hf.co/user/repo-GGUF:Q4_K_M.",
  other: "Hugging Face repo id, e.g. mlx-community/whisper-large-v3-turbo. Downloads to the HF cache.",
};

function modelDetail(model) {
  const detail = [model.parameters, model.quantization].filter(Boolean).join(" · ");
  return `${gb(model.size_bytes)}${detail ? ` · ${detail}` : ""}`;
}

async function setResidency(role, load, model) {
  const call = load ? loadRole : unloadRole;
  try {
    return await call({ role, model });
  } catch (error) {
    if (!load || !error.message.includes("force: true")) throw error;
    const reason = error.message.replace(/ Send force: true.*$/, "");
    if (!confirm(`${reason}\n\nLoad anyway?`)) return null;
    return call({ role, model, force: true });
  }
}

function useAction(onChanged) {
  const [busy, setBusy] = useState(false);
  const run = async (action, done) => {
    setBusy(true);
    try {
      const result = await action();
      if (result) onChanged(done);
    } catch (error) {
      onChanged(error.message);
    } finally {
      setBusy(false);
    }
  };
  return [busy, run];
}

export function RoleRow({ role, config, installed, onChanged }) {
  const names = (installed[config.runner] ?? []).map((model) => model.name);
  if (!names.includes(config.default)) names.unshift(config.default);
  const current = (installed[config.runner] ?? []).find((model) => model.name === config.default);
  const [busy, run] = useAction(onChanged);
  const load = () => run(() => setResidency(role, true), `loaded ${config.default}`);
  const unload = () => run(() => setResidency(role, false), `unloaded ${config.default}`);

  const choose = async (model) => {
    try {
      const result = await setRoleDefault({ role, model });
      onChanged(result.warnings?.join(" ") ?? "");
      loadModels();
    } catch (error) {
      onChanged(error.message);
    }
  };

  return (
    <tr>
      <td>{role}</td>
      <td class={styles.mono}>{config.runner}</td>
      <td>
        <select class={styles.tableSelect} value={config.default} onChange={(event) => choose(event.currentTarget.value)}>
          {names.map((name) => <option key={name} value={name}>{name}</option>)}
        </select>
      </td>
      <td class={styles.numeric}>{current ? gb(current.size_bytes) : "not downloaded"}</td>
      <td class={styles.actionCell}>
        <span class={styles.buttons}>
          <ui.Button small disabled={busy} onClick={load}>Load</ui.Button>
          <ui.Button small disabled={busy} onClick={unload}>Unload</ui.Button>
        </span>
      </td>
    </tr>
  );
}

export function InstalledRows({ catalog, onChanged }) {
  const roleFor = (runner) => Object.entries(catalog.roles).find(([, config]) => config.runner === runner)?.[0];
  const remove = async (runner, model) => {
    if (!confirm(`Delete ${model}? This frees its disk space; reinstalling downloads it again.`)) return;
    try {
      await deleteModel({ role: roleFor(runner), model });
      onChanged("");
    } catch (error) {
      onChanged(error.message);
    }
  };
  return Object.entries(catalog.installed).flatMap(([runner, models]) => models.map((model) => (
    <tr key={`${runner}:${model.name}`}>
      <td class={styles.mono}>{runner}</td>
      <td class={styles.mono}>{model.name}</td>
      <td class={styles.numeric}>{modelDetail(model)}</td>
      <td class={styles.actionCell}><ui.Button small variant="danger" onClick={() => remove(runner, model.name)}>Delete</ui.Button></td>
    </tr>
  )));
}

export function Search({ catalog, onPick }) {
  const roles = Object.keys(catalog.roles);
  const [role, setRole] = useState(roles[0]);
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
    <Panel title="Search Hugging Face" meta="only what this role's runtime can load">
      <ui.Grid>
        <ui.Field label="Role">
          <select value={role} onChange={(event) => { setRole(event.currentTarget.value); setResults(null); }}>
            {roles.map((name) => <option key={name} value={name}>{name}</option>)}
          </select>
        </ui.Field>
        <ui.Field label="Query">
          <input placeholder="blank for most downloaded" value={query} onInput={(event) => setQuery(event.currentTarget.value)} onKeyDown={(event) => event.key === "Enter" && search()} />
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
                  <td class={styles.actionCell}><ui.Button small onClick={() => onPick({ role, model: result.name })}>Select</ui.Button></td>
                </tr>
              ))}
            </tbody>
          </table>
        )
        : <ui.Empty>No supported models match.</ui.Empty>)}
    </Panel>
  );
}

export function Install({ catalog, pick, onInstalled }) {
  const roles = Object.keys(catalog.roles);
  const [role, setRole] = useState(roles[0]);
  const [model, setModel] = useState("");
  const [progress, setProgress] = useState(null);
  const [busy, setBusy] = useState(false);
  const runner = catalog.roles[role]?.runner;

  useEffect(() => {
    if (!pick) return;
    setRole(pick.role);
    setModel(pick.model);
  }, [pick]);

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
      setProgress({ status: `installed ${name} — pick it as a default above`, fraction: 1, warnings });
      onInstalled();
    } catch (error) {
      setProgress({ status: "failed", fraction: 0, error: error.message });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel title="Install" meta={runner}>
      <ui.Grid>
        <ui.Field label="Role">
          <select value={role} onChange={(event) => setRole(event.currentTarget.value)}>
            {roles.map((name) => <option key={name} value={name}>{name}</option>)}
          </select>
        </ui.Field>
        <ui.Field label="Model" hint={HINTS[runner] ?? HINTS.other}>
          <input placeholder="hf.co/user/repo-GGUF:Q4_K_M · ollama name · or HF repo id" value={model} onInput={(event) => setModel(event.currentTarget.value)} />
        </ui.Field>
      </ui.Grid>
      {progress && (
        <>
          <ui.Meter fraction={progress.fraction} />
          <ui.Meta>{progress.status}</ui.Meta>
          {progress.warnings?.length > 0 && <ui.Notice>{progress.warnings.join(" ")}</ui.Notice>}
          {progress.error && <ui.ErrorText>{progress.error}</ui.ErrorText>}
        </>
      )}
      <ui.Row end>
        <ui.Button variant="primary" disabled={busy || !model.trim()} onClick={install}>{busy ? "Installing…" : "Install"}</ui.Button>
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
  const [catalog, setCatalog] = useState(null);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [pick, setPick] = useState(null);
  const memory = useMemory(active);

  const refresh = async () => {
    try {
      setCatalog(await adminModels());
      setError("");
    } catch (failure) {
      setError(failure.message);
    }
  };

  useEffect(() => {
    if (active) refresh();
  }, [active]);

  const changed = (message) => {
    setNotice(message);
    refresh();
  };

  const installedCount = catalog
    ? Object.values(catalog.installed).reduce((total, models) => total + models.length, 0)
    : null;
  const ramUsed = memory ? memory.total_bytes - memory.available_bytes : null;

  const readout = [
    { label: "Roles", value: catalog ? Object.keys(catalog.roles).length : "—" },
    { label: "Installed", value: installedCount ?? "—" },
    { label: "RAM", value: memory ? `${gb(ramUsed)} / ${gb(memory.total_bytes)}` : "—" },
    { label: "Disk free", value: memory ? gb(memory.disk_free_bytes) : "—" },
  ];

  return (
    <Workbench single title="Models" subtitle="Pick each role's default, load and unload it, find models on Hugging Face, and manage the workers." readout={readout}>
      {error && <ui.ErrorText>{error}</ui.ErrorText>}
      {notice && <ui.Notice>{notice}</ui.Notice>}
      {!catalog && !error && <Panel title="Catalog"><ui.Empty><ui.Spinner /> loading catalog…</ui.Empty></Panel>}
      {catalog && (
        <>
          <Panel
            title="Defaults"
            meta="what each role serves"
            flush
            tools={<ui.Button small onClick={refresh}>Refresh</ui.Button>}
          >
            <table>
              <thead><tr><th>Role</th><th>Runner</th><th>Default</th><th>Size</th><th /></tr></thead>
              <tbody>
                {Object.entries(catalog.roles).map(([role, config]) => (
                  <RoleRow key={role} role={role} config={config} installed={catalog.installed} onChanged={changed} />
                ))}
              </tbody>
            </table>
          </Panel>
          <Search catalog={catalog} onPick={setPick} />
          <Install catalog={catalog} pick={pick} onInstalled={refresh} />
          <Memory memory={memory} roles={catalog.roles} onChanged={changed} />
          <Panel title="Installed" meta="on disk" flush>
            <table>
              <thead><tr><th>Runner</th><th>Name</th><th>Size</th><th /></tr></thead>
              <tbody><InstalledRows catalog={catalog} onChanged={changed} /></tbody>
            </table>
          </Panel>
        </>
      )}
    </Workbench>
  );
}
