import { useState } from "preact/hooks";
import { Icon } from "../components/Icon.jsx";
import * as store from "../store.js";
import * as control from "./Control.jsx";
import Models from "./Models.jsx";
import styles from "./Settings.module.css";

const GROUPS = [
  {
    title: "Control",
    sections: [{ value: "server", label: "Server", icon: "server" }],
  },
  {
    title: "Models",
    sections: [{ value: "models", label: "Models", icon: "models" }],
  },
];

const SECTION_VALUES = GROUPS.flatMap((group) => group.sections.map((section) => section.value));

export default function Settings() {
  const [section, setSection] = useState(() => {
    const saved = store.get("omni.settings", "server");
    return SECTION_VALUES.includes(saved) ? saved : "server";
  });

  const choose = (next) => {
    setSection(next);
    store.set("omni.settings", next);
  };

  return (
    <div class={styles.page}>
      <nav class={styles.nav} aria-label="Settings sections">
        {GROUPS.map((group) => (
          <div key={group.title} class={styles.group}>
            <h2 class={styles.groupTitle}>{group.title}</h2>
            {group.sections.map((entry) => (
              <button
                type="button"
                key={entry.value}
                class={entry.value === section ? [styles.item, styles.itemOn].join(" ") : styles.item}
                aria-current={entry.value === section ? "page" : undefined}
                onClick={() => choose(entry.value)}
              >
                <Icon name={entry.icon} />
                {entry.label}
              </button>
            ))}
          </div>
        ))}
      </nav>
      <div class={styles.content}>
        {section === "server" && <control.Server />}
        {section === "models" && <Models />}
      </div>
    </div>
  );
}
