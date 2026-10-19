import { useState } from "preact/hooks";
import { Icon } from "../components/Icon.jsx";
import { ROLE_META, ROLE_ORDER } from "../api/roles.js";
import * as store from "../store.js";
import * as control from "./Control.jsx";
import Models from "./Models.jsx";
import styles from "./Settings.module.css";

export default function Settings() {
  const [section, setSection] = useState(() => {
    const saved = store.get("omni.settings", "server");
    return saved === "models" ? "models" : "server";
  });
  const [focusRole, setFocusRole] = useState(() => store.get("omni.setupRole", null));

  const chooseServer = () => {
    setSection("server");
    store.set("omni.settings", "server");
  };

  const chooseModels = (role) => {
    setSection("models");
    store.set("omni.settings", "models");
    setFocusRole(role ?? null);
    store.set("omni.setupRole", role ?? null);
  };

  return (
    <div class={styles.page}>
      <nav class={styles.nav} aria-label="Settings sections">
        <div class={styles.group}>
          <h2 class={styles.groupTitle}>Control</h2>
          <button
            type="button"
            class={section === "server" ? [styles.item, styles.itemOn].join(" ") : styles.item}
            aria-current={section === "server" ? "page" : undefined}
            onClick={chooseServer}
          >
            <Icon name="server" />
            Server
          </button>
        </div>
        <div class={styles.group}>
          <h2 class={styles.groupTitle}>Models</h2>
          <button
            type="button"
            class={section === "models" && !focusRole ? [styles.item, styles.itemOn].join(" ") : styles.item}
            aria-current={section === "models" && !focusRole ? "page" : undefined}
            onClick={() => chooseModels(null)}
          >
            <Icon name="models" />
            All models
          </button>
          {ROLE_ORDER.map((role) => (
            <button
              type="button"
              key={role}
              class={[styles.item, styles.subitem, section === "models" && focusRole === role && styles.itemOn].filter(Boolean).join(" ")}
              aria-current={section === "models" && focusRole === role ? "page" : undefined}
              onClick={() => chooseModels(role)}
            >
              <Icon name={role} />
              {ROLE_META[role]?.label ?? role}
            </button>
          ))}
        </div>
      </nav>
      <div class={styles.content}>
        {section === "server" && <control.Server />}
        {section === "models" && <Models key={focusRole ?? "all"} />}
      </div>
    </div>
  );
}
