import { useEffect, useState } from "preact/hooks";
import * as api from "./api.js";
import * as store from "./store.js";
import * as ui from "./ui.jsx";
import { Icon } from "./icons.jsx";
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

function useTheme() {
  const [theme, setTheme] = useState(() => store.get("omni.theme", "system"));
  useEffect(() => {
    const media = matchMedia("(prefers-color-scheme: dark)");
    const apply = () => {
      const resolved = theme === "system" ? (media.matches ? "dark" : "light") : theme;
      document.documentElement.dataset.theme = resolved;
    };
    apply();
    store.set("omni.theme", theme);
    media.addEventListener("change", apply);
    return () => media.removeEventListener("change", apply);
  }, [theme]);
  return [theme, setTheme];
}

function GatewayStatus() {
  const health = api.health.value;
  if (!health) return <span class={styles.status}><ui.Spinner /> connecting</span>;
  if (!health.ok) return <span class={styles.status}><ui.Dot status="down" /> gateway down</span>;
  const workers = Object.values(health.workers);
  const ready = workers.filter((worker) => worker.status === "ready" || worker.status === "external").length;
  const status = ready === workers.length ? "ready" : "starting";
  return <span class={styles.status}><ui.Dot status={status} /> {ready} of {workers.length} workers ready</span>;
}

function LastRequest() {
  const last = api.lastRequest.value;
  if (!last) return <span class={styles.lastRequest}>No requests yet</span>;
  const failed = last.status >= 400;
  return (
    <span class={styles.lastRequest}>
      <span class={styles.route}>{last.route}</span>
      <span class={failed ? styles.failed : ""}>{last.status}</span>
      <span>{last.elapsed} ms</span>
    </span>
  );
}

export default function App() {
  const [tab, setTab] = useState(() => {
    const saved = store.get("omni.tab", "chat");
    return TABS.some((entry) => entry.id === saved) ? saved : "chat";
  });
  const [theme, setTheme] = useTheme();

  useEffect(() => {
    api.loadModels();
    api.pollHealth();
    const timer = setInterval(api.pollHealth, 3000);
    return () => clearInterval(timer);
  }, []);

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
          <div class={styles.section}>
            <div class={styles.sectionTitle}>{section.title}</div>
            {section.tabs.map((entry) => (
              <button
                type="button"
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
        {TABS.map((entry) => (
          <div hidden={entry.id !== tab}>
            <entry.component active={entry.id === tab} />
          </div>
        ))}
      </main>
    </div>
  );
}
