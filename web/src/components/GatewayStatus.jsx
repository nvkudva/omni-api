import { health, healthError } from "../api/health.js";
import { ROLE_META, runtime } from "../api/roles.js";
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
  const roles = Object.keys(ROLE_META).map((role) => runtime.value.roles[role]);
  const loaded = roles.filter((state) => state?.loaded).length;
  const status = loaded === roles.length ? "ready" : "starting";
  return <span class={styles.status}><ui.Dot status={status} /> {loaded} of {roles.length} models loaded · gateway ok</span>;
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
