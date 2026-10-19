import { useEffect, useRef, useState } from "preact/hooks";
import { loadModels } from "./api/models.js";
import { pollRuntime, ROLE_ORDER } from "./api/roles.js";
import { usePolling } from "./hooks/usePolling.js";
import { useTheme } from "./hooks/useTheme.js";
import { GatewayStatus, LastRequest } from "./components/GatewayStatus.jsx";
import { ConfirmHost } from "./components/Confirm.jsx";
import { RoleModelCard } from "./components/RoleModelCard.jsx";
import * as store from "./store.js";
import { Icon } from "./components/Icon.jsx";
import styles from "./App.module.css";
import Chat from "./tabs/Chat.jsx";
import Decide from "./tabs/Decide.jsx";
import Embed from "./tabs/Embed.jsx";
import Image from "./tabs/Image.jsx";
import Video from "./tabs/Video.jsx";
import Stt from "./tabs/Stt.jsx";
import Tts from "./tabs/Tts.jsx";
import Settings from "./tabs/Settings.jsx";

const COMPONENTS = { chat: Chat, decide: Decide, embed: Embed, image: Image, video: Video, stt: Stt, tts: Tts };
const TAB_IDS = [...ROLE_ORDER, "settings"];

// Models and Health used to be tabs of their own; they live in Settings now.
const RETIRED_TABS = { models: "settings", health: "settings" };

const THEME_ORDER = ["system", "light", "dark"];
const THEME_LABEL = { system: "Auto", light: "Light", dark: "Dark" };

function savedTab() {
  const saved = store.get("omni.tab", "chat");
  const tab = RETIRED_TABS[saved] ?? saved;
  return TAB_IDS.includes(tab) ? tab : "chat";
}

export default function App() {
  const [tab, setTab] = useState(savedTab);
  const [theme, setTheme] = useTheme();

  usePolling(loadModels, 30000, { immediate: true });

  // Pauses itself while the document is hidden.
  usePolling(pollRuntime, 4000);

  const cardsRef = useRef(null);
  useEffect(() => {
    cardsRef.current?.querySelector('[aria-current="page"]')?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, [tab]);

  const choose = (id) => {
    setTab(id);
    store.set("omni.tab", id);
  };

  // A card whose default cannot start: open Settings at the Models section with this role's card first.
  const openSetup = (role) => {
    store.set("omni.setupRole", role);
    store.set("omni.settings", "models");
    choose("settings");
  };

  const nextTheme = THEME_ORDER[(THEME_ORDER.indexOf(theme) + 1) % THEME_ORDER.length];
  const settingsOpen = tab === "settings";

  return (
    <div class={styles.shell}>
      <header class={styles.header}>
        <div class={styles.brand}>
          <img src={`${import.meta.env?.BASE_URL ?? "/"}favicon.svg`} alt="" width="26" height="26" />
          <span class={styles.brandName}>omni</span>
          <GatewayStatus />
        </div>
        <LastRequest />
        <div class={styles.actions}>
          <button
            type="button"
            class={styles.iconButton}
            title={`Theme: ${THEME_LABEL[theme]} (switch to ${THEME_LABEL[nextTheme]})`}
            aria-label={`Theme: ${THEME_LABEL[theme]}. Switch to ${THEME_LABEL[nextTheme]}`}
            onClick={() => setTheme(nextTheme)}
          >
            <Icon name={theme} size={16} />
          </button>
          <button
            type="button"
            class={`${styles.iconButton} ${settingsOpen ? styles.iconButtonOn : ""}`}
            title="Settings"
            aria-label="Settings"
            aria-current={settingsOpen ? "page" : undefined}
            onClick={() => choose("settings")}
          >
            <Icon name="settings" size={16} />
          </button>
        </div>
      </header>
      <nav ref={cardsRef} class={styles.cards} aria-label="Models">
        {ROLE_ORDER.map((role) => (
          <RoleModelCard key={role} density="mini" role={role} active={role === tab} onOpen={choose} onSetup={openSetup} />
        ))}
      </nav>
      <main class={styles.main}>
        {/* Every page stays mounted so an in-flight request survives a tab
            switch; the inactive ones just hide. */}
        {ROLE_ORDER.map((role) => {
          const RolePage = COMPONENTS[role];
          return (
            <div key={role} class={role === tab && !settingsOpen ? styles.page : styles.pageHidden} aria-hidden={role === tab && !settingsOpen ? undefined : true}>
              <RolePage active={role === tab && !settingsOpen} />
            </div>
          );
        })}
        {settingsOpen && <Settings key="settings" />}
      </main>
      <ConfirmHost />
    </div>
  );
}
