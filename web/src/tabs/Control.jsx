import { useEffect, useState } from "preact/hooks";
import { pollHealth } from "../api/health.js";
import { saveSetting, serverAction, serverInfo, serverLogs, workerAction } from "../api/endpoints.js";
import { pollRuntime, runtime } from "../api/roles.js";
import * as ui from "../components/ui.jsx";
import { Panel, Workbench } from "../components/workbench.jsx";
import { WorkerRow } from "./Models.jsx";
import styles from "./Control.module.css";

const RECONNECT_TIMEOUT_MS = 60000;
const LOG_LINES = 300;

const TUNABLES = [
  { key: "gateway.idle_unload_minutes", label: "Idle unload (minutes)", hint: "Unload a worker's model after this long unused. Restarts every worker.", number: true, step: "any" },
  { key: "tts.voice", label: "Default voice", hint: "Voice used when a speech request names none. Restarts tts.", number: false },
  { key: "image.steps", label: "Image steps", hint: "Denoising steps per image. More is slower and finer. Restarts image.", number: true, step: 1 },
  { key: "video.frames", label: "Video frames", hint: "Frames per clip. Restarts video.", number: true, step: 1 },
  { key: "video.fps", label: "Video fps", hint: "Playback rate of generated clips. Restarts video.", number: true, step: 1 },
];

const sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

const reachable = () => fetch("/health").then(() => true, () => false);

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
    </Panel>
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
        : <ui.Empty>Reading workers…</ui.Empty>}
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
      <div class={styles.tunable}>
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

export function Runtime() {
  const { info, error, refresh } = useInfo();
  return (
    <Workbench single title="Runtime" subtitle="Defaults the workers read at start. Saving writes ~/.omni-api/config.toml and restarts only the workers that use the value.">
      {error && <ui.ErrorText>settings request failed: {error}</ui.ErrorText>}
      <Panel title="Tunables">
        {info
          ? TUNABLES.map((spec) => <Tunable key={spec.key} spec={spec} stored={info.tunables[spec.key]} onSaved={refresh} />)
          : !error && <ui.Empty><ui.Spinner /> reading settings…</ui.Empty>}
      </Panel>
    </Workbench>
  );
}

export function Logs() {
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
  useEffect(() => { refresh(); }, []);

  return (
    <Workbench single title="Logs" subtitle="The tail of the gateway log.">
      {error && <ui.ErrorText>log request failed: {error}</ui.ErrorText>}
      <Panel title="omni.log" meta={path} tools={<ui.Button small disabled={loading} onClick={refresh}>Refresh</ui.Button>}>
        {lines.length ? <ui.Code>{lines.join("\n")}</ui.Code> : !error && <ui.Empty>The log is empty.</ui.Empty>}
      </Panel>
    </Workbench>
  );
}
