import { useEffect, useRef, useState } from "preact/hooks";
import { getText } from "../api/client.js";
import { OLLAMA_ROLES } from "../api/roles.js";
import { usePolling } from "../hooks/usePolling.js";
import styles from "./LogsCard.module.css";

const LOG_POLL_MS = 2000;
const LOG_LINES = 300;

/**
 * Full-width live log for one role, minimized until clicked. While open it tails
 * `/admin/logs/<role>` every two seconds and sticks to the newest line.
 */
export function LogsCard({ role }) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const viewRef = useRef(null);
  const fileName = OLLAMA_ROLES.has(role) ? "Ollama server.log" : `${role}.log`;

  usePolling(async () => {
    try {
      setText(await getText(`/admin/logs/${role}?lines=${LOG_LINES}`));
      setError("");
    } catch (failure) {
      setError(failure.message);
    }
  }, LOG_POLL_MS, { active: open });

  useEffect(() => {
    const view = viewRef.current;
    if (!view || !open) return;
    const nearBottom = view.scrollHeight - view.scrollTop - view.clientHeight < 80;
    if (nearBottom) view.scrollTop = view.scrollHeight;
  }, [text, open]);

  return (
    <section class={styles.logsCard}>
      <button type="button" class={styles.logsHead} aria-expanded={open} onClick={() => setOpen(!open)}>
        <span class={styles.logsCaret} aria-hidden="true">{open ? "▾" : "▸"}</span>
        <span class={styles.logsTitle}>Logs</span>
        <span class={styles.logsMeta}>{fileName} · live tail</span>
        <span class={styles.logsState}>{open ? (error ? "unavailable" : "tailing…") : "show"}</span>
      </button>
      {open && (
        <div class={styles.terminal}>
          <div class={styles.terminalBar} aria-hidden="true">
            <span class={`${styles.dot} ${styles.dotRed}`} />
            <span class={`${styles.dot} ${styles.dotYellow}`} />
            <span class={`${styles.dot} ${styles.dotGreen}`} />
            <span class={styles.terminalName}>{fileName}</span>
          </div>
          <pre ref={viewRef} class={styles.terminalBody}>{error || text || "loading…"}</pre>
        </div>
      )}
    </section>
  );
}
