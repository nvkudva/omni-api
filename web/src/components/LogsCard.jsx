import { useEffect, useRef, useState } from "preact/hooks";
import { cx } from "../lib/cx.js";
import { Icon } from "./Icon.jsx";
import * as ui from "./ui.jsx";
import { getText } from "../api/client.js";
import { OLLAMA_ROLES } from "../api/roles.js";
import { usePageActive } from "../hooks/usePageActive.js";
import { usePolling } from "../hooks/usePolling.js";
import styles from "./LogsCard.module.css";

const LOG_POLL_MS = 2000;
const LOG_LINES = 300;

/**
 * Full-width live log for one role, minimized until clicked. While open it tails
 * `/admin/logs/<role>` every two seconds and sticks to the newest line.
 */
export function LogsCard({ role, initialOpen = false }) {
  const [open, setOpen] = useState(initialOpen);
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const viewRef = useRef(null);
  const pageActive = usePageActive();
  const fileName = OLLAMA_ROLES.has(role) ? "Ollama server.log" : `${role}.log`;

  usePolling(async () => {
    try {
      setText(await getText(`/admin/logs/${role}?lines=${LOG_LINES}`));
      setError("");
    } catch (failure) {
      setError(failure.message);
    }
  }, LOG_POLL_MS, { active: open && pageActive });

  useEffect(() => {
    const view = viewRef.current;
    if (!view || !open) return;
    const nearBottom = view.scrollHeight - view.scrollTop - view.clientHeight < 80;
    if (nearBottom) view.scrollTop = view.scrollHeight;
  }, [text, open]);

  return (
    <section class={styles.logsCard}>
      <button type="button" class={styles.head} aria-expanded={open} onClick={() => setOpen(!open)}>
        <span class={cx(styles.caret, open && styles.open)}><Icon name="caret" size={12} /></span>
        <span class={styles.title}>Logs</span>
        <ui.Meta class={styles.meta}>{fileName} · live tail</ui.Meta>
        {open && <span class={styles.state}>{error ? "unavailable" : "tailing…"}</span>}
      </button>
      {open && (
        <div class={styles.terminal}>
          <pre ref={viewRef} class={styles.terminalBody}>{error || text || "loading…"}</pre>
        </div>
      )}
    </section>
  );
}
