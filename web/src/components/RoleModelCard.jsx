import { useEffect, useState } from "preact/hooks";
import { Icon } from "./Icon.jsx";
import { ask, withForce } from "./Confirm.jsx";
import { ROLE_META, isWorker, matchesModel, downloadFor, install, pollRuntime, runtime, setPinned, stopDownload } from "../api/roles.js";
import { notify } from "../api/notify.js";
import { deleteModel, loadRole, saveSetting, setRoleDefault, unloadRole, workerAction } from "../api/endpoints.js";
import { InstallProgress } from "./FindAndInstall.jsx";
import { HealthCard } from "./HealthCard.jsx";
import { LogsCard } from "./LogsCard.jsx";
import { gb, shortModel } from "../lib/format.js";
import { cx } from "../lib/cx.js";
import * as ui from "./ui.jsx";
import styles from "./RoleModelCard.module.css";

/**
 * One card, one status. A card is small enough that its light, its word and its button are read
 * together, so all three come from here: the dot can never say "stopped" beside the word
 * "running". Precedence runs from "can this run at all" to "is it being held for me":
 * a card that needs setup, then a runtime that is down, then one starting up, then what the
 * runtime holds, and last whether omni is keeping it there.
 */
function cardStatus(state, { checking, needsSetup, installing }) {
  if (checking) return "checking";
  if (needsSetup) return "blocked";
  if (installing) return "installing";
  const { status, loaded, pinned } = state;
  if (status === "stopped" || status === "unavailable") return "stopped";
  if (status === "failed") return "failed";
  if (status === "starting" || status === "restarting") return "starting";
  if (loaded) return pinned ? "warm" : "running";
  return pinned ? "loading" : "idle";
}

/**
 * What each status wears: a colour, the word on the card, and the tooltip that explains the word.
 * `warm` and `running` are both "in memory now"; they differ in whether it stays there, which is
 * exactly what the switch beside them decides.
 */
const CARD_STATUS = {
  checking: { tone: "busy", word: "checking…", hint: () => "waiting for the gateway" },
  blocked: { tone: "busy", word: "needs setup", hint: (state) => state.setupReason || "this role's default model cannot run on this machine" },
  installing: { tone: "busy", word: "installing…", hint: (state, download) => download?.error || `downloading ${download?.model ?? state.model}` },
  stopped: {
    tone: "bad",
    word: "off",
    hint: (state) => state.engineUrl
      ? `Ollama at ${state.engineUrl} is not answering, so this model cannot run`
      : "the worker is not running",
  },
  failed: { tone: "bad", word: "failed", hint: () => "the worker keeps failing; its log has the reason" },
  starting: { tone: "busy", word: "starting…", hint: () => "the worker is coming up" },
  loading: { tone: "busy", word: "loading model…", hint: (state) => `loading ${state.model} into memory` },
  warm: { tone: "ok", word: "loaded · kept", hint: (state) => `${state.runningModel ?? state.model} is in memory and kept loaded` },
  running: { tone: "ok", word: "loaded", hint: (state) => `${state.runningModel ?? state.model} is in memory; it unloads once it has been idle` },
  idle: { tone: "idle", word: "idle · not loaded", hint: (state) => `the worker is up but ${state.model} is not in memory; the next request loads it` },
};

const UNKNOWN = {
  model: null,
  loaded: false,
  pinned: false,
  runningModel: null,
  memoryBytes: 0,
  modelBytes: 0,
  status: "unknown",
  held: false,
};

const MINI_TITLE = { stt: "Speech to text", tts: "Text to speech" };

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
      notify(error.message, { title: `Could not keep ${ROLE_META[role]?.label ?? role} warm` });
    } finally {
      setBusy(false);
    }
  };
  return [busy, flip];
}

/**
 * The load switch every role shares. Load brings the role up if its worker is down, then
 * loads the model; Unload frees the model, and also stops keeping it warm, or the keep-warm loop
 * would load it straight back.
 */
function useLoadToggle(role, { onChanged, onProblem } = {}) {
  const [busy, setBusy] = useState(false);
  const label = ROLE_META[role]?.label ?? role;
  const toggle = async (event) => {
    event?.stopPropagation();
    const { model, loaded, status, pinned } = { ...runtime.value.roles[role], pinned: runtime.value.pins?.includes(role) };
    const wasStopped = status === "stopped" || status === "failed";
    setBusy(true);
    onProblem?.("");
    try {
      if (loaded) {
        await (pinned ? setPinned(role, false) : setResidency(role, false, model));
      } else {
        if (wasStopped) await workerAction(role, "start");
        try {
          await withForce((options) => setResidency(role, true, model, options), {
            title: `Load ${label} anyway?`,
            message: `${model} on this machine:`,
          });
        } catch (error) {
          // a worker that was just started is not answering yet; its first request loads the model
          if (!wasStopped) throw error;
        }
      }
      await pollRuntime();
      onChanged?.(loaded ? `unloaded ${model}` : `loading ${model}`);
    } catch (error) {
      onProblem?.(error.message);
      notify(error.message, { title: `Could not ${loaded ? "unload" : "load"} ${label}` });
    } finally {
      setBusy(false);
    }
  };
  return [busy, toggle];
}

/** A labelled on/off switch: the load switch in the mini footer and the detail header, and the
 *  keep-warm switch in the Settings box. */
export function WarmSwitch({ pinned, disabled, busy, title, label, onClick }) {
  return (
    <ui.Switch
      checked={pinned}
      aria-label={label}
      title={title}
      disabled={busy || disabled}
      onClick={onClick}
    />
  );
}

export function Section({ title, meta, role, tools, warmToggle, health, children }) {
  const roleMeta = role ? ROLE_META[role] : null;
  const [logOpen, setLogOpen] = useState(false);
  const [healthOpen, setHealthOpen] = useState(false);
  return (
    <section class={styles.section}>
      {(title || meta) && (
        <header class={roleMeta ? cx(styles.head, styles.role) : styles.head} style={roleMeta ? { "--role-color": roleMeta.color } : undefined}>
          {roleMeta
            ? (
              <>
                <div class={styles.headRow}>
                  <span class={ui.css.roleGlyph} style={{ "--role-color": roleMeta.color }}><Icon name={role} /></span>
                  {title && <h2 class={styles.title}>{title}</h2>}
                  <div class={styles.headSub}>
                    <span class={ui.css.endpoint} title={roleMeta.endpoint}>{roleMeta.endpoint}</span>
                    {meta && <ui.Meta>{meta}</ui.Meta>}
                  </div>
                  <div class={styles.tools}>
                    {tools}
                    <span class={styles.icons}>
                      {health && (
                        <ui.IconButton icon="health" label={`${title} health details`} aria-expanded={healthOpen} onClick={() => setHealthOpen(!healthOpen)} />
                      )}
                      <ui.IconButton icon="logs" label={logOpen ? "Hide this role's log" : "Show this role's log"} aria-expanded={logOpen} onClick={() => setLogOpen(!logOpen)} />
                    </span>
                    {warmToggle}
                  </div>
                </div>
              </>
            )
            : (
              <>
                <div class={ui.css.titleGroup}>
                  {title && <h2 class={styles.title}>{title}</h2>}
                  {meta && <ui.Meta>{meta}</ui.Meta>}
                </div>
                <div class={styles.tools}>{tools}</div>
              </>
            )}
        </header>
      )}
      {healthOpen && roleMeta && (
        <div class={styles.log}>
          <HealthCard role={role} label={title} />
        </div>
      )}
      {logOpen && roleMeta && (
        <div class={styles.log}>
          <LogsCard role={role} initialOpen />
        </div>
      )}
      {children && <div class={styles.body}>{children}</div>}
    </section>
  );
}

export function Tunable({ spec, stored, onSaved, inline }) {
  const [draft, setDraft] = useState(stored == null ? "" : String(stored));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState("");

  useEffect(() => { setDraft(stored == null ? "" : String(stored)); }, [stored]);

  const current = stored == null ? "" : String(stored);
  const write = async (value) => {
    const [section, key] = spec.key.split(".");
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

  // A fixed choice has nothing to confirm: picking an option saves it there and then.
  if (spec.options) {
    const choices = current !== "" && !spec.options.some((option) => String(option.value) === current)
      ? [...spec.options, { value: current, label: `${current} minutes` }]
      : spec.options;
    if (inline) {
      return (
        <label class={styles.inlineTunable} title={spec.hint}>
          <span>{spec.label}</span>
          <select
            aria-label={spec.label}
            value={current}
            disabled={busy}
            onChange={(event) => write(spec.number ? Number(event.currentTarget.value) : event.currentTarget.value)}
          >
            {choices.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
          </select>
          {(error || saved) && <ui.Meta>{error || saved}</ui.Meta>}
        </label>
      );
    }
    return (
      <ui.Field label={spec.label} hint={error || saved || spec.hint}>
        <div class={styles.tunable}>
          <select
            aria-label={spec.label}
            value={current}
            disabled={busy}
            onChange={(event) => write(spec.number ? Number(event.currentTarget.value) : event.currentTarget.value)}
          >
            {choices.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
          </select>
        </div>
      </ui.Field>
    );
  }

  const dirty = draft !== current;
  const save = () => write(spec.number ? Number(draft) : draft);

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
    { key: "video.fps", label: "Video fps", hint: "Playback rate of generated clips. Restarts video.", number: true, step: 1 },
  ],
};

/** Downloaded models first, smallest first; the rest keep the curated order. One row per model, downloaded or not. */
function modelRows(installed, recommended) {
  const rows = recommended.map((row) => ({
    name: row.model,
    note: row.note,
    gb: row.gb,
    tight: Boolean(row.blockers?.length),
    disk: installed.find((model) => matchesModel(model.name, row.model)) ?? null,
  }));
  for (const model of installed) {
    if (!rows.some((row) => row.disk === model)) rows.push({ name: model.name, disk: model });
  }
  const downloaded = rows.filter((row) => row.disk).sort((a, b) => a.disk.size_bytes - b.disk.size_bytes);
  return [...downloaded, ...rows.filter((row) => !row.disk)];
}

export function ModelList({ role, installed, recommended = [], onChanged, current, busy, canLoad, defaultMissing, onLoad, onUnload }) {
  const downloading = Boolean(downloadFor(role)?.busy);
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
  const makeDefault = async (model) => {
    try {
      await setRoleDefault({ role, model });
      onChanged(`${model} is now the ${role} default`);
    } catch (error) {
      onChanged(error.message);
    }
  };
  // A role with no working default gets the first model it downloads, so a fresh install ends up usable.
  const download = async (model) => {
    if (!(await install(role, model))) return;
    if (!defaultMissing || matchesModel(model, current)) return onChanged(`installed ${model}`);
    try {
      await setRoleDefault({ role, model });
      onChanged(`installed ${model}, now the ${role} default`);
    } catch (error) {
      onChanged(error.message);
    }
  };
  const rows = modelRows(installed, recommended);
  const running = runtime.value.roles[role];
  const isLoaded = (name) => Boolean(running?.loaded && running.runningModel && matchesModel(running.runningModel, name));
  if (!rows.length) return <ui.Empty>No models for this role yet.</ui.Empty>;
  return (
    <div class={styles.modelTable}>
      <table>
        <thead><tr><th>Models</th><th>Size</th><th /></tr></thead>
        <tbody>
          {rows.map((row) => {
            const isDefault = matchesModel(row.name, current);
            return (
              <tr key={row.name}>
                <td>
                  <span class={styles.nameCell}>
                    {row.disk ? (
                      <button
                        type="button"
                        class={cx(styles.radio, isDefault && styles.radioOn)}
                        aria-pressed={isDefault}
                        aria-label={isDefault ? `${row.name} is the default` : `Make ${row.name} the default`}
                        title={isDefault ? "Default model" : "Make default"}
                        disabled={busy || isDefault}
                        onClick={() => makeDefault(row.name)}
                      />
                    ) : <span class={styles.radioSpace} />}
                    <span>
                      {row.name}
                      {!row.disk && row.tight && <span class={styles.defaultTag}>tight</span>}
                      {row.note && <span class={styles.rowNote}>{row.note}</span>}
                    </span>
                  </span>
                </td>
                <td class={ui.css.numeric}>{row.disk ? modelDetail(row.disk) : row.gb ? `${row.gb} GB` : "—"}</td>
                <td class={ui.css.actionCell}>
                  <span class={styles.rowActions}>
                    {!row.disk && <ui.Button small class={styles.rowButton} disabled={busy || downloading} onClick={() => download(row.name)}>Download</ui.Button>}
                    {row.disk && isLoaded(row.name) && <ui.Button small class={styles.rowButton} disabled={busy} onClick={() => onUnload(row.name)}>Unload</ui.Button>}
                    {row.disk && !isLoaded(row.name) && <ui.Button small class={styles.rowButton} disabled={busy || !canLoad} onClick={() => onLoad(row.name)}>Load</ui.Button>}
                    {row.disk
                      ? <ui.IconButton icon="trash" danger label={`Delete ${row.name}`} title="Delete (frees disk space)" onClick={() => remove(row.name)} />
                      : <span class={styles.iconSpace} />}
                  </span>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function MiniCard({ role, active, onOpen, onSetup, onServer }) {
  const [problem, setProblem] = useState("");
  const state = runtime.value.roles[role] ?? { ...ROLE_META[role], role, ...UNKNOWN };

  const running = state.loaded;
  const pinned = state.pinned;
  const checking = state.status === "unknown";
  const setupState = state.setupState ?? "unknown";
  const needsSetup = !checking && (setupState === "missing" || setupState === "incompatible");
  const shownModel = state.runningModel ?? state.model ?? "—";
  const download = downloadFor(role);
  const installing = Boolean(download?.busy);
  const trouble = problem || download?.error || "";
  const status = cardStatus(state, { checking, needsSetup, installing });
  const look = CARD_STATUS[status];
  const word = installing ? download?.status ?? look.word : look.word;
  // Loading and starting have no byte count, so their bar pulses instead of filling.
  const working = installing || status === "loading" || status === "starting";
  const fraction = installing ? download.fraction || 0 : 0;
  const stopped = status === "stopped" || status === "failed";
  const [loadBusy, toggleLoad] = useLoadToggle(role, { onProblem: setProblem });

  // The one thing this card invites: download or set it up, open the server controls, or load or unload it.
  // The footer does not change shape between those beyond swapping its button, so the eye keeps
  // finding the light and the word in the same place on every card.
  const action = installing ? "stop"
    : needsSetup ? "setup"
    : stopped && !isWorker(role) ? "server"
    : "load";

  const stopDownloading = (event) => {
    event.stopPropagation();
    stopDownload(role);
  };

  const server = (event) => {
    event.stopPropagation();
    onServer?.();
  };

  const explain = async (event) => {
    event.stopPropagation();
    const reason = trouble || state.setupReason;
    // A model that is simply not on disk is the one case a click here can fix. Everything else —
    // a model this machine cannot run, a runtime that will not come up, a download that failed —
    // leads to the role's own card in Settings, where the log says what happened.
    const installable = needsSetup && state.setupState === "missing";
    const go = await ask({
      title: installable ? `${state.label} isn't ready` : `${state.label} needs attention`,
      message: reason,
      confirmLabel: installable ? "Install now" : "Open setup",
      cancelLabel: "Not now",
    });
    if (!go) return;
    setProblem("");
    if (installable) install(role, state.model);
    else onSetup?.(role);
  };

  const open = (event) => {
    if (event.target.closest("button")) return;
    onOpen(role);
  };

  return (
    <div
      class={cx(styles.miniCard, active && styles.on)}
      style={{ "--role-color": state.color }}
      aria-current={active ? "page" : undefined}
      onClick={open}
    >
      <span class={styles.top}>
        <span class={ui.css.roleGlyph} style={{ "--role-color": state.color }}><Icon name={role} /></span>
        <button type="button" class={styles.name} aria-label={`Open ${state.label}`} title={state.label} onClick={() => onOpen(role)}>{MINI_TITLE[role] ?? state.label}</button>
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
      <span class={styles.modelRow}>
        <span class={styles.model} title={shownModel}>{shortModel(shownModel)}</span>
        <span class={styles.route} title={state.endpoint}>{state.endpoint}</span>
        {state.modelBytes > 0 && <span class={styles.memory} title="model size on disk">{gb(state.modelBytes)}</span>}
      </span>
      <span class={styles.foot}>
        <span class={styles.status} title={look.hint(state, download)}>
          <ui.Dot tone={look.tone} />
          <span class={styles.word}>{word}</span>
        </span>
        {action === "stop" && (
          <ui.IconButton class={styles.footAction} icon="x" label={`Stop downloading ${download.model}`} size={14} onClick={stopDownloading} />
        )}
        {action === "setup" && (
          <ui.Button small disabled={installing} title={`Set up ${state.label}`} onClick={explain}>
            {state.setupState === "missing" ? "Download" : "Set up"}
          </ui.Button>
        )}
        {action === "server" && (
          <ui.IconButton class={styles.footAction} icon="server" label="Open the server controls" size={14} onClick={server} />
        )}
        {action === "load" && (
          <WarmSwitch
            pinned={state.loaded}
            busy={loadBusy}
            disabled={checking}
            label={`${state.loaded ? "unload" : "load"} ${state.label}`}
            title={checking ? "waiting for the gateway"
              : state.loaded ? `Unload ${shownModel}` : `Load ${state.model} into memory`}
            onClick={toggleLoad}
          />
        )}
      </span>
      {working && (
        <span
          class={cx(styles.download, !fraction && styles.waiting)}
          role="progressbar"
          aria-label={installing ? `Downloading ${download.model}` : word}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(fraction * 100)}
        >
          <span class={styles.downloadFill} style={{ width: `${Math.min(1, fraction) * 100}%` }} />
        </span>
      )}
    </div>
  );
}

function DetailCard({ role, entry, pinned, tunables, onChanged, onSaved, onAdd }) {
  const meta = ROLE_META[role];
  const label = meta?.label ?? role;
  const [busy, run] = useAction(onChanged);
  const [warmBusy, flipWarm] = useWarmToggle(role, entry.default, pinned, { onChanged });
  const [loadBusy, toggleLoad] = useLoadToggle(role, { onChanged });
  const status = runtime.value.roles[role]?.status;
  const loaded = Boolean(runtime.value.roles[role]?.loaded);
  const specs = (tunables && ROLE_TUNABLES[role]) || [];
  const workerRole = isWorker(role);

  const loadModel = (model) => run(async (options) => {
    if (model !== entry.default) await setRoleDefault({ role, model });
    await setResidency(role, true, model, options);
  }, `loaded ${model}`);
  const unloadModel = (model) => run(() => setResidency(role, false, model), `unloaded ${model}`);
  const installing = Boolean(downloadFor(role)?.busy);

  // A model can be on disk and still serve nothing: its worker may be stopped, or
  // Ollama may be down. Say which, and offer the one fix this card owns. A missing
  // model is already the install notice's story, so it wins over a stopped worker.
  const workerDown = (status === "stopped" || status === "failed") && entry.state !== "missing" && !entry.reason;
  const engineDown = status === "unavailable";
  const installDefault = async () => {
    if (await install(role, entry.default)) onChanged(`installed ${entry.default}`);
  };

  const restartWorker = () => run(() => workerAction(role, "restart"), `restarted the ${label} worker`);

  return (
    <Section
      title={meta?.label ?? role}
      role={role}
      health
      tools={(
        <>
          {workerRole && !workerDown && status !== "unknown" && <ui.Button small disabled={busy} onClick={restartWorker}>Restart</ui.Button>}
          <ui.Button small onClick={() => onAdd?.(role)}>Add model</ui.Button>
        </>
      )}
      warmToggle={(
        <>
          {entry.state === "missing"
            ? <ui.Button small disabled={installing} onClick={installDefault}>{installing ? "Downloading…" : "Download"}</ui.Button>
            : (
              <WarmSwitch
                pinned={loaded}
                busy={loadBusy}
                disabled={!status || status === "unknown" || entry.state !== "ready"}
                label={`${loaded ? "unload" : "load"} ${label}`}
                title={loaded ? `Unload ${entry.default}` : `Load ${entry.default} into memory`}
                onClick={toggleLoad}
              />
            )}
        </>
      )}
      meta={`${entry.runner} · ${entry.installed.length} downloaded · ${gb(entry.total_bytes)} · ${STATE_LABEL[entry.state] ?? entry.state}`}
    >
      {entry.reason && entry.state !== "missing" && <ui.Notice tone="error">{entry.reason}</ui.Notice>}
      {workerDown && (
        <ui.Notice tone="warn">{`${label} worker is stopped. Load starts it.`}</ui.Notice>
      )}
      {engineDown && (
        <ui.Notice tone="warn">{`Ollama is not answering, so ${label} cannot serve ${entry.default}. Restart it from the Server page.`}</ui.Notice>
      )}
      <InstallProgress role={role} />
      <ModelList
        role={role}
        installed={entry.installed}
        recommended={entry.recommended}
        onChanged={onChanged}
        current={entry.default}
        busy={busy}
        canLoad={entry.state === "ready"}
        defaultMissing={entry.state === "missing"}
        onLoad={loadModel}
        onUnload={unloadModel}
      />
      <div class={styles.settings}>
        <h4>Settings</h4>
        <div class={styles.tunable}>
          <WarmSwitch
            pinned={pinned}
            busy={warmBusy}
            disabled={entry.state !== "ready"}
            label={`${pinned ? "stop keeping warm" : "keep warm"} ${label}`}
            title={pinned ? `Stop keeping ${entry.default} warm` : `Keep ${entry.default} warm (loads it now and holds it)`}
            onClick={flipWarm}
          />
          <span>Keep warm: hold the model in memory instead of unloading it when idle</span>
        </div>
        {specs.map((spec) => <Tunable key={spec.key} spec={spec} stored={tunables[spec.key]} onSaved={onSaved} />)}
      </div>
    </Section>
  );
}

/**
 * One role, two densities. Mini: sidebar navigation card, opens the role's
 * tab, one status and the action that follows from it (Setup, Start, Server or
 * keep-warm). Detail: Settings card with current model, downloads, health and
 * per-role tunables. Both share keep-warm, install progress and setup state
 * through this module. Detail-only data (`entry`, `tunables`) comes from the
 * Settings page fetch; mini reads the shared `runtime` signal. Mini never mounts
 * detail children.
 */
export function RoleModelCard({ role, density = "mini", active, onOpen, onSetup, onServer, entry, pinned, tunables, onChanged, onSaved, onAdd }) {
  if (density === "detail") {
    return <DetailCard role={role} entry={entry} pinned={pinned} tunables={tunables} onChanged={onChanged} onSaved={onSaved} onAdd={onAdd} />;
  }
  return <MiniCard role={role} active={active} onOpen={onOpen} onSetup={onSetup} onServer={onServer} />;
}

export function roleMeta(role) {
  return ROLE_META[role];
}
