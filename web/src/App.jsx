import { useEffect, useState } from "preact/hooks";
import { pollHealth } from "./api/health.js";
import { loadModels } from "./api/models.js";
import { usePolling } from "./hooks/usePolling.js";
import { useTheme } from "./hooks/useTheme.js";
import { GatewayStatus, LastRequest } from "./components/GatewayStatus.jsx";
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

const SECTIONS = [
  { title: "Language", tabs: [
    { id: "chat", label: "Chat", color: "#0a84ff", component: Chat },
    { id: "decide", label: "Decide", color: "#bf5af2", component: Decide },
    { id: "embed", label: "Embed", color: "#30b0c7", component: Embed },
  ] },
  { title: "Media", tabs: [
    { id: "image", label: "Image", color: "#ff9f0a", component: Image },
    { id: "video", label: "Video", color: "#ff375f", component: Video },
  ] },
  { title: "Speech", tabs: [
    { id: "stt", label: "Speech to Text", color: "#ff453a", component: Stt },
    { id: "tts", label: "Text to Speech", color: "#30d158", component: Tts },
  ] },
  { title: "System", tabs: [
    { id: "models", label: "Models", color: "#8e8e93", component: Models },
    { id: "health", label: "Health", color: "#34c759", component: Health },
  ] },
];
const TABS = SECTIONS.flatMap((section) => section.tabs);
const THEMES = [
  { value: "system", label: "Auto" },
  { value: "light", label: "Light" },
  { value: "dark", label: "Dark" },
];

export default function App() {
  const [tab, setTab] = useState(() => {
    const saved = store.get("omni.tab", "chat");
    return TABS.some((entry) => entry.id === saved) ? saved : "chat";
  });
  const [theme, setTheme] = useTheme();

  useEffect(() => {
    loadModels();
  }, []);

  // Pauses itself while the document is hidden.
  usePolling(pollHealth, 3000);

  const choose = (id) => {
    setTab(id);
    store.set("omni.tab", id);
  };

  return (
    <div class={styles.shell}>
      <nav class={styles.sidebar} aria-label="Playground sections">
        <div class={styles.brand}>
          <img src={`${import.meta.env.BASE_URL}favicon.svg`} alt="" width="28" height="28" />
          <div>
            <div class={styles.brandName}>omni</div>
            <GatewayStatus />
          </div>
        </div>
        {SECTIONS.map((section) => (
          <div class={styles.section} key={section.title}>
            <div class={styles.sectionTitle}>{section.title}</div>
            {section.tabs.map((entry) => (
              <button
                type="button"
                key={entry.id}
                class={`${styles.navItem} ${entry.id === tab ? styles.navItemOn : ""}`}
                aria-current={entry.id === tab ? "page" : undefined}
                onClick={() => choose(entry.id)}
              >
                <span class={styles.navIcon} style={{ background: entry.color }}><Icon name={entry.id} /></span>
                {entry.label}
              </button>
            ))}
          </div>
        ))}
        <div class={styles.sidebarFooter}>
          <LastRequest />
          <ui.Segmented label="Appearance" options={THEMES} value={theme} onChange={setTheme} />
        </div>
      </nav>
      <main class={styles.main}>
        {TABS.map((entry) => entry.id === tab && <entry.component key={entry.id} active />)}
      </main>
    </div>
  );
}
