import { toasts, dismiss } from "../api/notify.js";
import { Icon } from "./Icon.jsx";
import * as ui from "./ui.jsx";
import { cx } from "../lib/cx.js";
import styles from "./Toasts.module.css";

/** The pop-up stack, top right. Mount once, at the app root. */
export function Toasts() {
  if (!toasts.value.length) return null;
  return (
    <div class={styles.stack}>
      {toasts.value.map((toast) => (
        <div key={toast.id} role={toast.tone === "error" ? "alert" : "status"} class={cx(styles.toast, styles[toast.tone])}>
          <Icon name="warn" size={16} />
          <div class={styles.text}>
            {toast.title && <strong>{toast.title}</strong>}
            <span>{toast.message}</span>
          </div>
          <ui.IconButton icon="x" label="Dismiss" size={14} onClick={() => dismiss(toast.id)} />
        </div>
      ))}
    </div>
  );
}
