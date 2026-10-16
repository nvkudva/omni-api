import { useState } from "preact/hooks";
import { Icon } from "./Icon.jsx";
import { ask } from "./Confirm.jsx";
import { ROLE_META, runtime, setPinned } from "../api/roles.js";
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
 * The card opens the role's page; the switch stands alone, so a click on it must
 * not also navigate.
 */
export function RoleCard({ role, active, onOpen }) {
  const [busy, setBusy] = useState(false);
  const state = runtime.value.roles[role] ?? { ...ROLE_META[role], role, ...UNKNOWN };

  const running = state.loaded;
  const pinned = state.pinned;
  const checking = state.status === "unknown";
  const shownModel = state.runningModel ?? state.model ?? "—";
  // a pinned model that is not in memory yet is on its way there, not idle
  const word = pinned && !running ? "loading…" : STATUS_WORD[state.status] ?? state.status;

  const flip = async (event) => {
    event.stopPropagation();
    const next = !pinned;
    setBusy(true);
    try {
      await setPinned(role, next);
    } catch (error) {
      if (error.code !== "overload") {
        // Publish the failure where the readout already lives.
        runtime.value = { ...runtime.value, error: error.message };
      } else {
        // Something about this model stands in the way. Show it, and let the person decide.
        const go = await ask({
          title: `Keep ${state.label} warm anyway?`,
          message: `${state.model} on this machine:`,
          reasons: error.blockers,
          advisories: runtime.value.warnings,
          confirmLabel: "Load anyway",
          cancelLabel: "Not now",
        });
        if (go) {
          try {
            await setPinned(role, next, { force: true });
          } catch (forced) {
            runtime.value = { ...runtime.value, error: forced.message };
          }
        }
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      class={cx(styles.card, active && styles.on, !running && !active && styles.off)}
      role="button"
      tabIndex={0}
      aria-current={active ? "page" : undefined}
      onClick={() => onOpen(role)}
      onKeyDown={(event) => {
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
          {word}
        </span>
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
      </span>
    </div>
  );
}

export function roleMeta(role) {
  return ROLE_META[role];
}
