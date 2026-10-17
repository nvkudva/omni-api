import { useEffect, useState } from "preact/hooks";
import { pollHealth } from "../api/health.js";
import { serverAction, serverInfo, serverLogs, workerAction } from "../api/endpoints.js";
import { pollRuntime, runtime } from "../api/roles.js";
import * as ui from "../components/ui.jsx";
import { Panel, Workbench } from "../components/workbench.jsx";
import { WorkerRow } from "./Models.jsx";
import styles from "./Control.module.css";

const RECONNECT_TIMEOUT_MS = 60000;
const LOG_LINES = 300;

const sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

const reachable = () => fetch("/health").then((response) => response.ok, () => false);

async function waitForGateway(wantUp) {
  const deadline = Date.now() + RECONNECT_TIMEOUT_MS;
  while (Date.now() < deadline) {
    await sleep(1000);
    if ((await reachable()) === wantUp) return true;
  }
  return false;
}

function duration(seconds) {
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} h ${minutes % 60} min`;
  return `${Math.floor(hours / 24)} d ${hours % 24} h`;
}

function useInfo() {
  const [info, setInfo] = useState(null);
  const [error, setError] = useState("");
  const refresh = async () => {
    try {
      setInfo(await serverInfo());
      setError("");
    } catch (failure) {
      setError(failure.message);
    }
  };
  useEffect(() => { refresh(); }, []);
  return { info, error, refresh };
}

function Facts({ rows }) {
  return (
    <dl class={styles.facts}>
      {rows.map(([label, value]) => (
        <div key={label}><dt>{label}</dt><dd>{value}</dd></div>
      ))}
    </dl>
  );
}

function Gateway({ info, onReconnected }) {
  const [phase, setPhase] = useState("");
  const [error, setError] = useState("");

  const run = async (action) => {
    const verb = action === "restart" ? "Restart" : "Stop";
    if (!window.confirm(`${verb} the gateway? In-flight requests will be dropped.`)) return;
    setError("");
    try {
      await serverAction(action);
    } catch (failure) {
      setError(failure.message);
      return;
    }
    setPhase(action === "restart" ? "restarting" : "stopping");
    const wentDown = await waitForGateway(false);
    if (action === "stop") {
      setPhase(wentDown ? "stopped" : "");
      if (!wentDown) setError("the gateway did not stop in time; check the logs");
      return;
    }
    const cameBack = wentDown && await waitForGateway(true);
    setPhase("");
    if (cameBack) onReconnected();
    else setError("the gateway did not come back in time; check the logs");
  };

  const working = phase === "restarting" || phase === "stopping";
  const started = info ? duration(Date.now() / 1000 - info.gateway.started_at) : "";
  return (
    <Panel
      title="Gateway"
      meta={info ? `pid ${info.gateway.pid}` : ""}
      tools={(
        <>
          <ui.Button small disabled={working || phase === "stopped"} onClick={() => run("restart")}>Restart</ui.Button>
          <ui.Button small variant="danger" disabled={working || phase === "stopped"} onClick={() => run("stop")}>Stop</ui.Button>
        </>
      )}
    >
      {error && <ui.ErrorText>{error}</ui.ErrorText>}
      {working && <ui.Notice tone="info"><ui.Spinner /> {phase === "restarting" ? "Restarting… the page reconnects on its own." : "Stopping…"}</ui.Notice>}
      {phase === "stopped" && <ui.Notice>The gateway is stopped. Run <code>omni up</code> in a terminal to start it again.</ui.Notice>}
      {info && (
        <Facts rows={[
          ["Address", `${info.gateway.host}:${info.gateway.port}`],
          ["Version", info.gateway.version],
          ["Running for", started],
          ["Config", info.paths.config],
          ["Home", info.paths.home],
        ]}
        />
      )}
      <GatewayLogs />
    </Panel>
  );
}

/** The gateway log tail, collapsed until opened; loads on first open. */
function GatewayLogs() {
  const [lines, setLines] = useState([]);
  const [path, setPath] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const refresh = async () => {
    setLoading(true);
    try {
      const result = await serverLogs(LOG_LINES);
      setLines(result.lines);
      setPath(result.path);
      setError("");
    } catch (failure) {
      setError(failure.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <details
      class={styles.logs}
      onToggle={(event) => {
        if (event.currentTarget.open && !lines.length && !error && !loading) refresh();
      }}
    >
      <summary>
        Gateway log
        {path && <span class={styles.logsPath}>{path}</span>}
        <span class={styles.logsTools}>
          <ui.IconButton
            icon="refresh"
            label="Refresh log"
            disabled={loading}
            onClick={(event) => {
              event.preventDefault();
              refresh();
            }}
          />
        </span>
      </summary>
      {error && <ui.ErrorText>log request failed: {error}</ui.ErrorText>}
      {lines.length
        ? <ui.Code>{lines.join("\n")}</ui.Code>
        : !error && <ui.Meta>{loading ? "reading…" : "The log is empty."}</ui.Meta>}
    </details>
  );
}

function Ollama({ info, onChanged }) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const { ollama } = info;

  const control = async (action) => {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await workerAction("chat", action);
      setMessage(`ollama: ${action} sent`);
      await onChanged();
    } catch (failure) {
      setError(failure.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel
      title="Ollama"
      meta={ollama.reachable ? `v${ollama.version}` : "unreachable"}
      tools={(
        <>
          <ui.Button small disabled={busy || !ollama.managed || ollama.reachable} onClick={() => control("start")}>Start</ui.Button>
          <ui.Button small variant="danger" disabled={busy || !ollama.managed || !ollama.reachable} onClick={() => control("stop")}>Stop</ui.Button>
          <ui.Button small disabled={busy || !ollama.managed} onClick={() => control("restart")}>Restart</ui.Button>
        </>
      )}
    >
      {error && <ui.ErrorText>{error}</ui.ErrorText>}
      {message && <ui.Meta>{message}</ui.Meta>}
      {!ollama.managed && (
        <ui.Notice tone="info">
          Ollama was already running when the gateway started, so omni does not own it. Restart it where it runs
          (for example <code>brew services restart ollama</code>, or quit and reopen the Ollama app).
        </ui.Notice>
      )}
      <Facts rows={[["URL", ollama.url], ["Status", ollama.reachable ? "reachable" : "unreachable"], ["Managed by omni", ollama.managed ? "yes" : "no"]]} />
    </Panel>
  );
}

function Workers() {
  const workers = runtime.value.memory?.workers ?? {};
  const entries = Object.entries(workers);
  return (
    <Panel title="Workers" meta="stt · tts · image · video" flush>
      {entries.length
        ? (
          <table>
            <thead><tr><th>Worker</th><th>Status</th><th>CPU</th><th>Memory</th><th /></tr></thead>
            <tbody>
              {entries.map(([name, worker]) => <WorkerRow key={name} name={name} worker={worker} onChanged={pollRuntime} />)}
            </tbody>
          </table>
        )
        : <ui.Empty>No workers reported. If the gateway is down, restart it above.</ui.Empty>}
    </Panel>
  );
}

export function Server() {
  const { info, error, refresh } = useInfo();
  const reconnected = () => {
    refresh();
    pollHealth();
    pollRuntime();
  };
  return (
    <Workbench single title="Server" subtitle="Start, stop and restart the gateway, Ollama and the workers.">
      {error && <ui.ErrorText>server info failed: {error}</ui.ErrorText>}
      {info && <Gateway info={info} onReconnected={reconnected} />}
      {info && <Ollama info={info} onChanged={async () => { await refresh(); await pollRuntime(); }} />}
      <Workers />
    </Workbench>
  );
}

