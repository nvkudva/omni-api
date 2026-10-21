import { useEffect, useState } from "preact/hooks";
import { getText } from "../api/client.js";
import { pollHealth } from "../api/health.js";
import { serverAction, serverInfo, serverLogs, workerAction } from "../api/endpoints.js";
import { pollRuntime, runtime } from "../api/roles.js";
import { ask } from "../components/Confirm.jsx";
import * as ui from "../components/ui.jsx";
import { Panel, Workbench } from "../components/workbench.jsx";
import { Memory } from "./Models.jsx";
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
    <dl class={ui.css.facts}>
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
    const restart = action === "restart";
    const confirmed = await ask({
      title: restart ? "Restart the gateway?" : "Shut down the gateway?",
      message: restart
        ? "In-flight requests will be dropped. This page reconnects on its own."
        : "This page will close and every worker stops, so in-flight requests are dropped and models omni loaded are unloaded. Run omni up in a terminal to start omni again.",
      confirmLabel: restart ? "Restart" : "Shutdown",
      tone: "danger",
    });
    if (!confirmed) return;
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
      if (!wentDown) {
        setError("the gateway did not stop in time; check the logs");
        return;
      }
      // a tab the browser opened itself refuses this, and the notice below says what to do next
      if (typeof window !== "undefined" && typeof window.close === "function") window.close();
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
      meta={info ? `pid ${info.gateway.pid} · port ${info.gateway.port}${info.gateway.version ? ` · v${info.gateway.version}` : ""} · up ${started}` : ""}
      tools={(
        <>
          <ui.Button small disabled={working || phase === "stopped"} onClick={() => run("restart")}>Restart</ui.Button>
          <ui.Button small disabled={working || phase === "stopped"} onClick={() => run("stop")}>Shutdown</ui.Button>
        </>
      )}
    >
      {error && <ui.ErrorText>{error}</ui.ErrorText>}
      {working && <ui.Notice tone="info"><ui.Spinner /> {phase === "restarting" ? "Restarting… the page reconnects on its own." : "Stopping…"}</ui.Notice>}
      {phase === "stopped" && <ui.Notice>This tab can be closed. Run <code>omni up</code> in a terminal to start the gateway again.</ui.Notice>}
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
      <OllamaLogs />
    </Panel>
  );
}

/** The engine log: one Ollama serves chat, decide and embed, so its log belongs to no one role. */
function OllamaLogs() {
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const refresh = async () => {
    setLoading(true);
    try {
      setText(await getText(`/admin/logs/ollama?lines=${LOG_LINES}`));
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
        if (event.currentTarget.open && !text && !error && !loading) refresh();
      }}
    >
      <summary>
        Ollama engine log
        <span class={styles.logsPath}>one server for chat, decide and embed</span>
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
      {text
        ? <ui.Code>{text}</ui.Code>
        : !error && <ui.Meta>{loading ? "reading…" : "The log is empty."}</ui.Meta>}
    </details>
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
  const reason = ollama.managed ? undefined : "Ollama runs outside omni — manage it with `ollama serve`";

  const control = async (action) => {
    if (action !== "start") {
      const confirmed = await ask({
        title: `${action === "stop" ? "Stop" : "Restart"} Ollama?`,
        message: "Chat, decide and embed requests, and any model loading or generating right now, will be interrupted.",
        confirmLabel: action === "stop" ? "Stop Ollama" : "Restart Ollama",
        tone: "danger",
      });
      if (!confirmed) return;
    }
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
          <ui.Button small title={reason} disabled={busy || !ollama.managed || ollama.reachable} onClick={() => control("start")}>Start</ui.Button>
          <ui.Button small title={reason} disabled={busy || !ollama.managed || !ollama.reachable} onClick={() => control("stop")}>Stop</ui.Button>
          <ui.Button small title={reason} disabled={busy || !ollama.managed} onClick={() => control("restart")}>Restart</ui.Button>
        </>
      )}
    >
      {error && <ui.ErrorText>{error}</ui.ErrorText>}
      {message && <ui.Meta>{message}</ui.Meta>}
      {!ollama.managed && (
        <ui.Notice tone="info">
          Ollama runs outside omni, so Start, Stop and Restart are disabled — manage it with <code>ollama serve</code>.
          It was already running when the gateway started. To restart it
          use, for example, <code>brew services restart ollama</code>, or quit and reopen the Ollama app.
        </ui.Notice>
      )}
      <Facts rows={[["URL", ollama.url], ["Status", ollama.reachable ? "reachable" : "unreachable"], ["Managed by omni", ollama.managed ? "yes" : "no"]]} />
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
  const memory = runtime.value.memory;
  const roles = Object.fromEntries(
    Object.entries(runtime.value.roles).map(([role, entry]) => [role, { default: entry.model }]),
  );
  return (
    <Workbench class={styles.server} single title="Server" subtitle="Start, stop and restart the gateway, Ollama and the workers.">
      {error && <ui.ErrorText>server info failed: {error}</ui.ErrorText>}
      {info && <Gateway info={info} onReconnected={reconnected} />}
      {info && <Ollama info={info} onChanged={async () => { await refresh(); await pollRuntime(); }} />}
      <Memory memory={memory} roles={roles} onChanged={pollRuntime} />
    </Workbench>
  );
}

