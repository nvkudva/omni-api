import { health } from "../api/health.js";
import { OLLAMA_ROLES } from "../api/roles.js";
import * as ui from "./ui.jsx";
import styles from "./HealthCard.module.css";

/**
 * One role's runtime readout in its own card, opened by the health button in a
 * model card's header (beside the log button). It reads the shared health poll;
 * Ollama-served roles have no worker of their own, so they point at the server.
 */
export function HealthCard({ role, label }) {
  const worker = health.value?.workers?.[role];
  return (
    <section class={styles.healthCard} aria-label={`${label ?? role} health details`}>
      <header class={styles.healthHead}>
        <span class={styles.healthTitle}>Health</span>
        <span class={styles.healthMeta}>{label ?? role}</span>
      </header>
      {worker
        ? (
          <dl class={styles.readout}>
            <div><dt>Status</dt><dd><ui.Dot status={worker.status} />{worker.status}{worker.held ? " (held)" : ""}</dd></div>
            <div><dt>Loaded</dt><dd>{worker.loaded ? (worker.model || "loaded") : "—"}</dd></div>
            <div><dt>Process</dt><dd>{worker.pid ?? worker.url ?? "—"}</dd></div>
            <div><dt>Restarts</dt><dd>{worker.restarts ?? "—"}</dd></div>
          </dl>
        )
        : (
          <div class={styles.empty}>
            <ui.Meta>
              {OLLAMA_ROLES.has(role)
                ? "Served by Ollama — see the Ollama card under Server."
                : "No health data yet."}
            </ui.Meta>
          </div>
        )}
    </section>
  );
}
