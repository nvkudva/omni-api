import { useState } from "preact/hooks";
import { Icon } from "./Icon.jsx";
import { ROLE_META, runtime, toggleRole } from "../api/roles.js";
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
const UNKNOWN = { model: null, loaded: false, runningModel: null, memoryBytes: 0, status: "unknown", held: false };

function StatusDot({ status, loaded }) {
  if (loaded) return <Dot status="ready" />;
  if (status === "starting" || status === "restarting" || status === "unknown") return <Dot status="starting" />;
  return <Dot status="stopped" />;
}

/**
 * One role in the sidebar: what it is, where its endpoint lives, which model is
 * serving it right now, how much memory that model holds, and a switch that
 * loads or unloads it. The card opens the role's page; the switch stands alone.
 */
export function RoleCard({ role, active, onOpen }) {
  const [busy, setBusy] = useState(false);
  const state = runtime.value.roles[role] ?? { ...ROLE_META[role], role, ...UNKNOWN };

  const running = state.loaded;
  const checking = state.status === "unknown";
  const shownModel = state.runningModel ?? state.model ?? "—";
  const word = STATUS_WORD[state.status] ?? state.status;

  const flip = async (event) => {
    event.stopPropagation();
    setBusy(true);
    try {
      await toggleRole(role, !running);
    } catch (error) {
      // Publish the failure where the readout already lives.
      runtime.value = { ...runtime.value, error: error.message };
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
          aria-checked={running}
          aria-label={`${running ? "unload" : "load"} ${state.label}`}
          title={checking ? "waiting for the gateway" : running ? `Unload ${shownModel}` : `Load ${state.model}`}
          disabled={busy || checking}
          class={cx(styles.switch, running && styles.switchOn)}
          onClick={flip}
        />
      </span>
    </div>
  );
}

export function roleMeta(role) {
  return ROLE_META[role];
}
