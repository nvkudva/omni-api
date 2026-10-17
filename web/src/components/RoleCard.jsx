import { useState } from "preact/hooks";
import { Icon } from "./Icon.jsx";
import { ask, withForce } from "./Confirm.jsx";
import { ROLE_META, install, installs, runtime, setPinned } from "../api/roles.js";
import { gb } from "../lib/format.js";
import { cx } from "../lib/cx.js";
import { Dot } from "./ui.jsx";
import styles from "./RoleCard.module.css";

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

// What a card shows before the first runtime poll answers (or while the gateway
// is unreachable): the role's own metadata, with nothing claimed about the model.
const UNKNOWN = {
  model: null,
  loaded: false,
  pinned: false,
  runningModel: null,
  memoryBytes: 0,
  status: "unknown",
  held: false,
};

function StatusDot({ status, loaded }) {
  if (loaded) return <Dot status="ready" />;
  if (status === "starting" || status === "restarting" || status === "unknown") return <Dot status="starting" />;
  return <Dot status="stopped" />;
}

/**
 * One role in the sidebar: what it is, where its endpoint lives, which model is
 * serving it right now, how much memory that model holds, and a switch that keeps
 * the model warm — on loads it and holds it, off unloads it.
 *
 * When the default is not on disk (or its runtime refuses it) the switch would
 * promise what it cannot do, so a Setup button takes its place. The card never
 * prints the error: a warning badge marks the problem, and Setup (or the badge)
 * explains it in a popup with the one action that clears it — install the model
 * here, or open the setup page when the runtime refuses it. The card opens the
 * role's page; the controls stand alone, so a click on any must not navigate.
 */
export function RoleCard({ role, active, onOpen, onSetup }) {
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");
  const state = runtime.value.roles[role] ?? { ...ROLE_META[role], role, ...UNKNOWN };

  const running = state.loaded;
  const pinned = state.pinned;
  const checking = state.status === "unknown";
  // the default can actually start: on disk and accepted by its runtime; while the
  // status call has not answered yet, the switch stays (it can only refuse a real load)
  const setupState = state.setupState ?? "unknown";
  const needsSetup = !checking && (setupState === "missing" || setupState === "incompatible");
  const shownModel = state.runningModel ?? state.model ?? "—";
  // a pinned model that is not in memory yet is on its way there, not idle
  const word = pinned && !running ? "loading…" : STATUS_WORD[state.status] ?? state.status;
  const download = installs.value[role];
  const installing = Boolean(download?.busy);
  const trouble = problem || download?.error || "";

  const flip = async (event) => {
    event.stopPropagation();
    setBusy(true);
    setProblem("");
    try {
      await withForce((options) => setPinned(role, !pinned, options), {
        title: `Keep ${state.label} warm anyway?`,
        message: `${state.model} on this machine:`,
      });
    } catch (error) {
      setProblem(error.message);
    } finally {
      setBusy(false);
    }
  };

  // What stands in the way, and the one action that clears it: install the model,
  // or (when its runtime refuses it) go and pick another on the setup page.
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

  // The switch is a button inside the clickable card: clicks and keys from it
  // must never also open the page.
  const open = (event) => {
    if (event.target.closest("button")) return;
    onOpen(role);
  };

  return (
    <div
      class={cx(styles.card, active && styles.on, !running && !active && styles.off)}
      role="button"
      tabIndex={0}
      aria-current={active ? "page" : undefined}
      onClick={open}
      onKeyDown={(event) => {
        if (event.target.closest("button")) return;
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onOpen(role);
        }
      }}
    >
      <span class={styles.top}>
        <span class={styles.badge} style={{ background: state.color }}><Icon name={role} /></span>
        <span class={styles.name}>{state.label}</span>
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
        <span class={styles.model} title={shownModel}>{shownModel}</span>
        <span class={styles.memory}>{running ? gb(state.memoryBytes) : "—"}</span>
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
              class={styles.setup}
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
              disabled={busy || checking}
              class={cx(styles.switch, pinned && styles.switchOn)}
              onClick={flip}
            />
          )}
      </span>
{/* No error text on the card: the warning badge says there is a problem, and Setup explains it. */}
    </div>
  );
}

export function roleMeta(role) {
  return ROLE_META[role];
}
