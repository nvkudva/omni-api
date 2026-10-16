import { useState } from "preact/hooks";
import { Icon } from "../components/Icon.jsx";
import * as store from "../store.js";
import * as ui from "../components/ui.jsx";
import { Panel, Workbench } from "../components/workbench.jsx";
import * as control from "./Control.jsx";
import Health from "./Health.jsx";
import Models from "./Models.jsx";
import styles from "./Settings.module.css";

const GROUPS = [
  {
    title: "Control",
    sections: [
      { value: "server", label: "Server", icon: "server" },
      { value: "runtime", label: "Runtime", icon: "sliders" },
      { value: "logs", label: "Logs", icon: "logs" },
    ],
  },
  {
    title: "Inspect",
    sections: [
      { value: "models", label: "Models", icon: "models" },
      { value: "health", label: "Health", icon: "health" },
    ],
  },
  {
    title: "Interface",
    sections: [{ value: "appearance", label: "Appearance", icon: "appearance" }],
  },
];

const SECTION_VALUES = GROUPS.flatMap((group) => group.sections.map((section) => section.value));

const THEMES = [
  { value: "system", label: "Auto" },
  { value: "light", label: "Light" },
  { value: "dark", label: "Dark" },
];

function Appearance({ theme, onTheme }) {
  return (
    <Workbench single title="Appearance" subtitle="Follow the system, or pin the playground to light or dark.">
      <Panel title="Theme">
        <ui.Segmented label="Appearance" options={THEMES} value={theme} onChange={onTheme} />
      </Panel>
    </Workbench>
  );
}

export default function Settings({ theme, onTheme }) {
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
        {section === "runtime" && <control.Runtime />}
        {section === "logs" && <control.Logs />}
        {section === "models" && <Models />}
        {section === "health" && <Health />}
        {section === "appearance" && <Appearance theme={theme} onTheme={onTheme} />}
      </div>
    </div>
  );
}
