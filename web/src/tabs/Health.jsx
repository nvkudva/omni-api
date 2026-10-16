import { health, healthError, pollHealth } from "../api/health.js";
import { loadModels, models } from "../api/models.js";
import * as ui from "../components/ui.jsx";
import { Panel, Workbench } from "../components/workbench.jsx";
import styles from "./tabs.module.css";

const READY = new Set(["ready", "external"]);

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

export default function Health() {
  const value = health.value;
  const error = healthError.value;
  const refresh = () => {
    pollHealth();
    loadModels();
  };

  const workers = Object.values(value?.workers ?? {});
  const ready = workers.filter((worker) => READY.has(worker.status)).length;

  const readout = [
    { label: "Gateway", value: value ? (value.ok ? "healthy" : "degraded") : error ? "unreachable" : "checking…" },
    { label: "Workers", value: workers.length ? `${ready} / ${workers.length} ready` : "—" },
    { label: "Models", value: models.value.data.length || "—" },
    { label: "Endpoint", value: "GET /health" },
  ];

  return (
    <Workbench single title="Health" subtitle="Worker processes, their pids, and the models each role serves." readout={readout}>
      {error && <ui.ErrorText>health request failed: {error}</ui.ErrorText>}
      <Panel title="Workers" flush tools={<ui.Button small onClick={refresh}>Refresh</ui.Button>}>
        {value
          ? (
            <table>
              <thead><tr><th>Worker</th><th>Status</th><th>Model loaded</th><th>pid / url</th><th>Restarts</th></tr></thead>
              <tbody><WorkerRows workers={value.workers ?? {}} /></tbody>
            </table>
          )
          : error
            ? <ui.Empty>health unavailable</ui.Empty>
            : <ui.Empty><ui.Spinner /> checking gateway…</ui.Empty>}
      </Panel>
      <Panel title="Models" meta="what /v1/models lists" flush>
        <table>
          <thead><tr><th>Role</th><th>Model</th></tr></thead>
          <tbody><ModelRows /></tbody>
        </table>
      </Panel>
      <Panel title="Raw">
        <ui.Code>{`GET /health\n${JSON.stringify(value, null, 2)}`}</ui.Code>
      </Panel>
    </Workbench>
  );
}
