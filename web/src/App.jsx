import { useState } from "preact/hooks";
import { loadModels } from "./api/models.js";
import { pollRuntime, ROLE_META } from "./api/roles.js";
import { usePolling } from "./hooks/usePolling.js";
import { useTheme } from "./hooks/useTheme.js";
import { GatewayStatus, LastRequest } from "./components/GatewayStatus.jsx";
import { ConfirmHost } from "./components/Confirm.jsx";
import { RoleCard } from "./components/RoleCard.jsx";
import * as store from "./store.js";
import * as ui from "./components/ui.jsx";
import { Icon } from "./components/Icon.jsx";
import styles from "./App.module.css";
import Chat from "./tabs/Chat.jsx";
import Decide from "./tabs/Decide.jsx";
import Embed from "./tabs/Embed.jsx";
import Image from "./tabs/Image.jsx";
import Video from "./tabs/Video.jsx";
import Stt from "./tabs/Stt.jsx";
import Tts from "./tabs/Tts.jsx";
import Models from "./tabs/Models.jsx";
import Health from "./tabs/Health.jsx";

const ROLE_SECTIONS = [
  { title: "Language", roles: ["chat", "decide", "embed"] },
  { title: "Media", roles: ["image", "video"] },
  { title: "Speech", roles: ["stt", "tts"] },
];

const SYSTEM = [
  { id: "models", label: "Models", color: "#8e8e93", component: Models },
  { id: "health", label: "Health", color: "#34c759", component: Health },
];

const COMPONENTS = {
  chat: Chat,
  decide: Decide,
  embed: Embed,
  image: Image,
  video: Video,
  stt: Stt,
  tts: Tts,
  ...Object.fromEntries(SYSTEM.map((entry) => [entry.id, entry.component])),
};
const TAB_IDS = [...Object.keys(ROLE_META), ...SYSTEM.map((entry) => entry.id)];

const THEMES = [
  { value: "system", label: "Auto" },
  { value: "light", label: "Light" },
  { value: "dark", label: "Dark" },
];

export default function App() {
  const [tab, setTab] = useState(() => {
    const saved = store.get("omni.tab", "chat");
    return TAB_IDS.includes(saved) ? saved : "chat";
  });
  const [theme, setTheme] = useTheme();
  const Page = COMPONENTS[tab] ?? Chat;

  usePolling(loadModels, 30000, { immediate: true });

  // Pauses itself while the document is hidden.
  usePolling(pollRuntime, 4000);

  const choose = (id) => {
    setTab(id);
    store.set("omni.tab", id);
  };

  return (
    <div class={styles.shell}>
      <nav class={styles.sidebar} aria-label="Playground sections">
        <div class={styles.brand}>
          <img src={`${import.meta.env?.BASE_URL ?? "/"}favicon.svg`} alt="" width="28" height="28" />
          <div>
            <div class={styles.brandName}>omni</div>
            <GatewayStatus />
          </div>
        </div>
        {ROLE_SECTIONS.map((section) => (
          <div class={styles.section} key={section.title}>
            <div class={styles.sectionTitle}>{section.title}</div>
            <div class={styles.cards}>
              {section.roles.map((role) => (
                <RoleCard key={role} role={role} active={role === tab} onOpen={choose} />
              ))}
            </div>
          </div>
        ))}
        <div class={styles.section}>
          <div class={styles.sectionTitle}>System</div>
          {SYSTEM.map((entry) => (
            <button
              type="button"
              key={entry.id}
              class={`${styles.sysItem} ${entry.id === tab ? styles.sysOn : ""}`}
              aria-current={entry.id === tab ? "page" : undefined}
              onClick={() => choose(entry.id)}
            >
              <span class={styles.sysIcon} style={{ background: entry.color }}><Icon name={entry.id} /></span>
              {entry.label}
            </button>
          ))}
        </div>
        <div class={styles.sidebarFooter}>
          <LastRequest />
          <ui.Segmented label="Appearance" options={THEMES} value={theme} onChange={setTheme} />
        </div>
      </nav>
      <main class={styles.main}>
        <Page key={tab} active />
      </main>
      <ConfirmHost />
    </div>
  );
}
