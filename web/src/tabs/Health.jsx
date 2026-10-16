import * as api from "../api.js";
import * as ui from "../ui.jsx";
import styles from "./tabs.module.css";

function WorkerRows({ workers }) {
  return Object.entries(workers).map(([name, worker]) => (
    <tr>
      <td><span class={styles.nameCell}><ui.Dot status={worker.status} />{name}</span></td>
      <td>{worker.status}</td>
      <td class={styles.mono}>{worker.pid ?? worker.url ?? "—"}</td>
      <td>{worker.restarts ?? "—"}</td>
    </tr>
  ));
}

function ModelRows() {
  const { data, defaults } = api.models.value;
  return data.map((model) => (
    <tr>
      <td>{model.role}</td>
      <td class={styles.mono}>{model.id}{defaults[model.role] === model.id ? "  (default)" : ""}</td>
    </tr>
  ));
}

export default function Health() {
  const health = api.health.value;
  const refresh = () => {
    api.pollHealth();
    api.loadModels();
  };

  return (
    <ui.Page title="Health" subtitle="Worker processes and the models they serve." actions={<ui.Button onClick={refresh}>Refresh</ui.Button>}>
      <ui.Stack>
        <ui.Card title="Workers" flush>
          <table>
            <thead><tr><th>Worker</th><th>Status</th><th>pid / url</th><th>Restarts</th></tr></thead>
            <tbody>{health && <WorkerRows workers={health.workers ?? {}} />}</tbody>
          </table>
        </ui.Card>
        <ui.Card title="Models" flush>
          <table>
            <thead><tr><th>Role</th><th>Model</th></tr></thead>
            <tbody><ModelRows /></tbody>
          </table>
        </ui.Card>
        <ui.Card title="Raw">
          <ui.Code>{`GET /health\n${JSON.stringify(health, null, 2)}`}</ui.Code>
        </ui.Card>
      </ui.Stack>
    </ui.Page>
  );
}
