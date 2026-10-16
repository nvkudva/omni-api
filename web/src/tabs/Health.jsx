import { health, healthError, pollHealth } from "../api/health.js";
import { loadModels, models } from "../api/models.js";
import * as ui from "../components/ui.jsx";
import styles from "./tabs.module.css";

function WorkerRows({ workers }) {
  return Object.entries(workers).map(([name, worker]) => (
    <tr key={name}>
      <td><span class={styles.nameCell}><ui.Dot status={worker.status} />{name}</span></td>
      <td>{worker.status}</td>
      <td class={styles.mono}>{worker.pid ?? worker.url ?? "—"}</td>
      <td>{worker.restarts ?? "—"}</td>
    </tr>
  ));
}

function ModelRows() {
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

  return (
    <ui.Page title="Health" subtitle="Worker processes and the models they serve." actions={<ui.Button onClick={refresh}>Refresh</ui.Button>}>
      <ui.Stack>
        {error && <ui.ErrorText>health request failed: {error}</ui.ErrorText>}
        <ui.Card title="Workers" flush={!!value}>
          {value ? (
            <table>
              <thead><tr><th>Worker</th><th>Status</th><th>pid / url</th><th>Restarts</th></tr></thead>
              <tbody><WorkerRows workers={value.workers ?? {}} /></tbody>
            </table>
          ) : error ? (
            <ui.Empty>health unavailable</ui.Empty>
          ) : (
            <ui.Empty><ui.Spinner /> checking gateway…</ui.Empty>
          )}
        </ui.Card>
        <ui.Card title="Models" flush>
          <table>
            <thead><tr><th>Role</th><th>Model</th></tr></thead>
            <tbody><ModelRows /></tbody>
          </table>
        </ui.Card>
        <ui.Card title="Raw">
          <ui.Code>{`GET /health\n${JSON.stringify(value, null, 2)}`}</ui.Code>
        </ui.Card>
      </ui.Stack>
    </ui.Page>
  );
}
