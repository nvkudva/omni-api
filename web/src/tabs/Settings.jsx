import { Icon } from "../components/Icon.jsx";
import { ROLE_META } from "../api/roles.js";
import * as control from "./Control.jsx";
import Models from "./Models.jsx";
import styles from "./Settings.module.css";

export default function Settings({ section = "server", role = null, backTo = "chat", onNavigate, onBack }) {
  const go = (next) => onNavigate({ tab: "settings", section: next, role: null });

  return (
    <div class={styles.page}>
      <nav class={styles.nav} aria-label="Settings sections">
        <button type="button" class={styles.back} onClick={onBack}>
          <span aria-hidden="true">←</span> Back to {ROLE_META[backTo]?.label ?? backTo}
        </button>
        <div class={styles.group}>
          <h2 class={styles.groupTitle}>Settings</h2>
          <button
            type="button"
            class={section === "server" ? [styles.item, styles.itemOn].join(" ") : styles.item}
            aria-current={section === "server" ? "page" : undefined}
            onClick={() => go("server")}
          >
            <Icon name="server" />
            Server
          </button>
          <button
            type="button"
            class={section === "models" ? [styles.item, styles.itemOn].join(" ") : styles.item}
            aria-current={section === "models" ? "page" : undefined}
            onClick={() => go("models")}
          >
            <Icon name="models" />
            Models
          </button>
        </div>
      </nav>
      <div class={styles.content}>
        {section === "server" && <control.Server />}
        {section === "models" && <Models role={role} onRole={(next) => onNavigate({ tab: "settings", section: "models", role: next })} />}
      </div>
    </div>
  );
}
