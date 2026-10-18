import { useEffect, useState } from "preact/hooks";
import { Icon } from "./Icon.jsx";
import { ask, withForce } from "./Confirm.jsx";
import { OLLAMA_ROLES, ROLE_META, install, installs, runtime, setPinned } from "../api/roles.js";
import { deleteModel, loadRole, saveSetting, setRoleDefault, unloadRole } from "../api/endpoints.js";
import { InstallProgress } from "./FindAndInstall.jsx";
import { LogsCard } from "./LogsCard.jsx";
import { health } from "../api/health.js";
import { gb, shortModel } from "../lib/format.js";
import { cx } from "../lib/cx.js";
import * as ui from "./ui.jsx";
import styles from "./RoleModelCard.module.css";
import tabStyles from "../tabs/tabs.module.css";

const STATUS_WORD = {
  ready: "running",
  external: "running",
  loaded: "running",
  idle: "idle",
  starting: "starting",
  restarting: "restarting",
  stopped: "stopped",
  unknown: "checking…",
};

const UNKNOWN = {
  model: null,
  loaded: false,
  pinned: false,
  runningModel: null,
  memoryBytes: 0,
  status: "unknown",
  held: false,
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

/** Shared keep-warm action for both densities. Error surface differs: mini keeps
 *  it on the card (`onProblem`), detail reports through the page (`onChanged`). */
function useWarmToggle(role, model, pinned, { onChanged, onProblem } = {}) {
  const [busy, setBusy] = useState(false);
  const flip = async () => {
    setBusy(true);
    onProblem?.("");
    try {
      const result = await withForce((options) => setPinned(role, !pinned, options), {
        title: `Keep ${ROLE_META[role]?.label ?? role} warm anyway?`,
        message: `${model} on this machine:`,
      });
      if (result && onChanged) onChanged(pinned ? `stopped keeping ${model} warm` : `keeping ${model} warm`);
    } catch (error) {
      if (onProblem) onProblem(error.message);
      else onChanged?.(error.message);
    } finally {
      setBusy(false);
    }
  };
  return [busy, flip];
}

/** Detail-density keep-warm control. */
export function WarmSwitch({ role, model, pinned, disabled, onChanged }) {
  const [busy, flip] = useWarmToggle(role, model, pinned, { onChanged });
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

export function Section({ title, meta, role, tools, warm, children }) {
  const roleMeta = role ? ROLE_META[role] : null;
  const [logOpen, setLogOpen] = useState(false);
  return (
    <section class={styles.setup}>
      {(title || meta) && (
        <header
          class={roleMeta ? [styles.setupHead, styles.setupHeadColor].join(" ") : styles.setupHead}
          style={roleMeta ? { background: `color-mix(in srgb, ${roleMeta.color} 13%, var(--surface))` } : undefined}
        >
          {roleMeta
            ? (
              <>
                <div class={styles.setupHeadRow}>
                  <span class={styles.badge} style={{ background: roleMeta.color }}><Icon name={role} /></span>
                  {title && <h2 class={styles.setupTitle}>{title}</h2>}
                  {warm && <span class={styles.warm} title="kept warm: this model stays loaded">warm</span>}
                  <div class={styles.setupTools}>
                    {tools}
                    <ui.IconButton icon="logs" label={logOpen ? "Hide this role's log" : "Show this role's log"} aria-expanded={logOpen} onClick={() => setLogOpen(!logOpen)} />
                  </div>
                </div>
                <div class={styles.setupHeadSub}>
                  <span class={styles.setupEndpoint} title={roleMeta.endpoint}>{roleMeta.endpoint}</span>
                  {meta && <span class={styles.setupMeta}>{meta}</span>}
                </div>
              </>
            )
            : (
              <>
                <div class={styles.setupTitleGroup}>
                  {title && <h2 class={styles.setupTitle}>{title}</h2>}
                  {meta && <span class={styles.setupMeta}>{meta}</span>}
                </div>
                <div class={styles.setupTools}>{tools}</div>
              </>
            )}
        </header>
      )}
      {logOpen && roleMeta && (
        <div class={styles.setupLog}>
          <LogsCard role={role} initialOpen />
        </div>
      )}
      <div class={styles.setupBody}>{children}</div>
    </section>
  );
}

export function Tunable({ spec, stored, onSaved }) {
  const [draft, setDraft] = useState(stored == null ? "" : String(stored));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState("");

  useEffect(() => { setDraft(stored == null ? "" : String(stored)); }, [stored]);

  const dirty = draft !== (stored == null ? "" : String(stored));
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
            <td>{model.name}</td>
            <td class={tabStyles.numeric}>{modelDetail(model)}</td>
            <td class={tabStyles.actionCell}>
              <ui.IconButton icon="trash" danger label={`Delete ${model.name}`} title="Delete (frees disk space)" onClick={() => remove(model.name)} />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

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
    <dl class={styles.health}>
      <div><dt>Status</dt><dd><ui.Dot status={worker.status} />{worker.status}{worker.held ? " (held)" : ""}</dd></div>
      <div><dt>Loaded</dt><dd>{worker.loaded ? (worker.model || "loaded") : "—"}</dd></div>
      <div><dt>Process</dt><dd>{worker.pid ?? worker.url ?? "—"}</dd></div>
      <div><dt>Restarts</dt><dd>{worker.restarts ?? "—"}</dd></div>
    </dl>
  );
}

function StatusDot({ status, loaded }) {
  if (loaded) return <ui.Dot status="ready" />;
  if (status === "starting" || status === "restarting" || status === "unknown") return <ui.Dot status="starting" />;
  return <ui.Dot status="stopped" />;
}

function MiniCard({ role, active, onOpen, onSetup }) {
  const [problem, setProblem] = useState("");
  const state = runtime.value.roles[role] ?? { ...ROLE_META[role], role, ...UNKNOWN };

  const running = state.loaded;
  const pinned = state.pinned;
  const checking = state.status === "unknown";
  const setupState = state.setupState ?? "unknown";
  const needsSetup = !checking && (setupState === "missing" || setupState === "incompatible");
  const shownModel = state.runningModel ?? state.model ?? "—";
  const word = pinned && !running ? "loading…" : STATUS_WORD[state.status] ?? state.status;
  const download = installs.value[role];
  const installing = Boolean(download?.busy);
  const trouble = problem || download?.error || "";
  const [warmBusy, flipWarm] = useWarmToggle(role, state.model, pinned, { onProblem: setProblem });

  const flip = (event) => {
    event.stopPropagation();
    flipWarm();
  };

  const explain = async (event) => {
    event.stopPropagation();
    const reason = trouble || state.setupReason;
    if (state.setupState === "incompatible") {
      const go = await ask({
        title: `${state.label} can't run this model`,
        message: reason,
        confirmLabel: "Open setup",
        cancelLabel: "Not now",
      });
      if (go) onSetup?.(role);
      return;
    }
    const go = await ask({
      title: `${state.label} isn't ready`,
      message: reason,
      confirmLabel: "Install now",
      cancelLabel: "Not now",
    });
    if (go) {
      setProblem("");
      install(role, state.model);
    }
  };

  const open = (event) => {
    if (event.target.closest("button")) return;
    onOpen(role);
  };

  return (
    <div
      class={cx(styles.card, active && styles.on, !running && !active && styles.off)}
      aria-current={active ? "page" : undefined}
      onClick={open}
    >
      <span class={styles.top}>
        <span class={styles.badge} style={{ background: state.color }}><Icon name={role} /></span>
        <button type="button" class={styles.name} aria-label={`Open ${state.label}`} onClick={() => onOpen(role)}>{state.label}</button>
        {pinned && <span class={styles.warm} title="kept warm: this model stays loaded">warm</span>}
        {(needsSetup || trouble) && (
          <button
            type="button"
            class={styles.warnBadge}
            aria-label={`${state.label} needs attention`}
            title={trouble || state.setupReason}
            onClick={explain}
          >
            <Icon name="warn" size={11} />
          </button>
        )}
        <span class={styles.runner}>{state.runner}</span>
      </span>
      <span class={styles.endpoint} title={state.endpoint}>{state.endpoint}</span>
      <span class={styles.modelRow}>
        <span class={styles.model} title={shownModel}>{shortModel(shownModel)}</span>
        {running && <span class={styles.memory}>{gb(state.memoryBytes)}</span>}
      </span>
      <span class={styles.foot}>
        <span class={styles.status}>
          <StatusDot status={state.status} loaded={running} />
          {installing ? download.status : word}
        </span>
        {needsSetup
          ? (
            <button
              type="button"
              class={styles.setupButton}
              disabled={installing}
              onClick={explain}
            >
              Setup
            </button>
          )
          : (
            <button
              type="button"
              role="switch"
              aria-checked={pinned}
              aria-label={`${pinned ? "stop keeping warm" : "keep warm"} ${state.label}`}
              title={checking ? "waiting for the gateway"
                : pinned ? `Stop keeping ${shownModel} warm` : `Keep ${state.model} warm (loads it now and holds it)`}
              disabled={warmBusy || checking}
              class={cx(styles.switch, pinned && styles.switchOn)}
              onClick={flip}
            />
          )}
      </span>
    </div>
  );
}

function DetailCard({ role, entry, pinned, tunables, onChanged, onSaved, onAdd }) {
  const meta = ROLE_META[role];
  const names = entry.installed.map((model) => model.name);
  if (!names.includes(entry.default)) names.unshift(entry.default);
  const current = entry.installed.find((model) => model.name === entry.default);
  const [busy, run] = useAction(onChanged);
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
      warm={pinned}
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
      <div class={styles.line} title={current ? `On disk · ${modelDetail(current)}` : "Not on disk"}>
        <select aria-label="Current model" value={entry.default} onChange={(event) => choose(event.currentTarget.value)}>
          {names.map((name) => <option key={name} value={name}>{name}</option>)}
        </select>
        <ui.Button small disabled={busy || entry.state !== "ready"} onClick={load}>Load</ui.Button>
        <ui.Button small disabled={busy} onClick={unload}>Unload</ui.Button>
        <WarmSwitch role={role} model={entry.default} pinned={pinned} disabled={entry.state !== "ready"} onChanged={onChanged} />
      </div>
      <DownloadedList role={role} installed={entry.installed} onChanged={onChanged} />
      {specs.map((spec) => <Tunable key={spec.key} spec={spec} stored={tunables[spec.key]} onSaved={onSaved} />)}
      <ui.Row>
        <ui.Button small variant="primary" onClick={() => onAdd?.(role)}>Add model</ui.Button>
      </ui.Row>
    </Section>
  );
}

/**
 * One role, two densities. Mini: top-strip navigation card, opens the role's
 * tab, keep-warm switch, Setup prompt when the default cannot run. Detail:
 * Settings card with current model, downloads, health and per-role tunables.
 * Both share keep-warm, install progress and setup state through this module.
 * Detail-only data (`entry`, `tunables`) comes from the Settings page fetch;
 * mini reads the shared `runtime` signal. Mini never mounts detail children.
 */
export function RoleModelCard({ role, density = "mini", active, onOpen, onSetup, entry, pinned, tunables, onChanged, onSaved, onAdd }) {
  if (density === "detail") {
    return <DetailCard role={role} entry={entry} pinned={pinned} tunables={tunables} onChanged={onChanged} onSaved={onSaved} onAdd={onAdd} />;
  }
  return <MiniCard role={role} active={active} onOpen={onOpen} onSetup={onSetup} />;
}

export function roleMeta(role) {
  return ROLE_META[role];
}
