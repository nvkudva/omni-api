import { health, healthError } from "../api/health.js";
import { lastRequest } from "../api/client.js";
import * as ui from "./ui.jsx";
import styles from "../App.module.css";

export function GatewayStatus() {
  const value = health.value;
  if (!value) {
    return healthError.value
      ? <span class={styles.status}><ui.Dot status="down" /> gateway down</span>
      : <span class={styles.status}><ui.Spinner /> connecting</span>;
  }
  if (!value.ok) return <span class={styles.status}><ui.Dot status="down" /> gateway down</span>;
  const workers = Object.values(value.workers ?? {});
  if (workers.length === 0) return <span class={styles.status}><ui.Dot status="starting" /> no workers</span>;
  const ready = workers.filter((worker) => worker.status === "ready" || worker.status === "external").length;
  const status = ready === workers.length ? "ready" : "starting";
  return <span class={styles.status}><ui.Dot status={status} /> {ready} of {workers.length} workers ready</span>;
}

export function LastRequest() {
  const last = lastRequest.value;
  if (!last) return <span class={styles.lastRequest}>No requests yet</span>;
  const failed = last.status >= 400;
  return (
    <span class={styles.lastRequest}>
      <span class={styles.route}>{last.route}</span>
      <span class={failed ? styles.failed : ""}>{last.status}</span>
      <span>{last.elapsed} ms</span>
    </span>
  );
}
