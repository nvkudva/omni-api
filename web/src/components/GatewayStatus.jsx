import { signal } from "@preact/signals";
import { health, healthError } from "../api/health.js";
import { ROLE_META, runtime } from "../api/roles.js";
import { lastRequest } from "../api/client.js";
import { gb } from "../lib/format.js";
import * as ui from "./ui.jsx";
import styles from "../App.module.css";

export function GatewayStatus() {
  const value = health.value;
  if (!value) {
    return healthError.value
      ? <span class={styles.status}><ui.Dot status="down" /> gateway down</span>
      : <span class={styles.status}><ui.Spinner /> connecting</span>;
  }
  const roles = Object.keys(ROLE_META).map((role) => runtime.value.roles[role]);
  const loaded = roles.filter((state) => state?.loaded).length;
  const status = loaded === roles.length ? "ready" : "starting";
  // The dot already says whether the gateway is up, so the chip only counts
  // what is in memory.
  return <span class={styles.status}><ui.Dot status={status} /> {loaded} of {roles.length} loaded</span>;
}

function Meter({ label, percent, title }) {
  const clamped = Math.min(100, Math.max(0, percent));
  return (
    <span class={styles.meter} title={title}>
      {label}
      <span class={styles.meterTrack}><span class={styles.meterFill} style={{ width: `${clamped}%` }} /></span>
      {Math.round(clamped)}%
    </span>
  );
}

export function SystemUsage() {
  const memory = runtime.value.memory;
  if (!memory?.total_bytes) return null;
  const usedBytes = memory.total_bytes - memory.available_bytes;
  return (
    <span class={styles.usage}>
      <Meter label="CPU" percent={memory.cpu_percent ?? 0} title={`${memory.cpu_count} cores`} />
      <Meter label="RAM" percent={(100 * usedBytes) / memory.total_bytes}
        title={`${gb(usedBytes)} used, ${gb(memory.available_bytes)} available of ${gb(memory.total_bytes)}`} />
    </span>
  );
}

const ollamaBannerClosed = signal(false);

/** A header banner when Ollama is missing or not answering: chat and embed need it, the other roles do not. */
export function OllamaBanner() {
  const chat = health.value?.workers?.chat;
  const isDown = chat && ["unavailable", "stopped", "failed"].includes(chat.status);
  if (!isDown || ollamaBannerClosed.value) return null;
  const cause = chat.installed === false
    ? "Ollama is not installed (run `omni init` to install it)"
    : "Ollama is not running (start it with `ollama serve`, or run `omni init`)";
  return (
    <div class={styles.banner} role="alert">
      <span>{cause}. Without it you cannot use Chat or Embed; the other models still work.</span>
      <ui.IconButton icon="x" label="Dismiss" size={14} onClick={() => { ollamaBannerClosed.value = true; }} />
    </div>
  );
}

export function LastRequest() {
  const last = lastRequest.value;
  if (!last) return null;
  const failed = last.status >= 400;
  return (
    <span class={styles.lastRequest}>
      <span class={styles.route}>{last.route}</span>
      <span class={failed ? styles.failed : ""}>{last.status}</span>
      <span>{last.elapsed} ms</span>
    </span>
  );
}
