import { useEffect, useRef, useState } from "preact/hooks";
import { loadModels } from "./api/models.js";
import { pollDownloads, pollRuntime, ROLE_ORDER } from "./api/roles.js";
import { pollExecs } from "./api/execs.js";
import * as ui from "./components/ui.jsx";
import { cx } from "./lib/cx.js";
import { PageActive } from "./hooks/usePageActive.js";
import { usePolling } from "./hooks/usePolling.js";
import { useTheme } from "./hooks/useTheme.js";
import { GatewayStatus, LastRequest, OllamaBanner, SystemUsage } from "./components/GatewayStatus.jsx";
import { ConfirmHost } from "./components/Confirm.jsx";
import { Toasts } from "./components/Toasts.jsx";
import { RoleModelCard } from "./components/RoleModelCard.jsx";
import * as store from "./store.js";
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

const THEME_ORDER = ["system", "light", "dark"];
const THEME_LABEL = { system: "Auto", light: "Light", dark: "Dark" };

function savedTab() {
  const saved = store.get("omni.tab", "chat");
  return ROLE_ORDER.includes(saved) ? saved : "chat";
}

/** The tab, Settings section and Models role filter a URL path names; "/" and unknown paths fall back. */
export function parsePath(path) {
  const [first, second, third] = path.split("/").filter(Boolean);
  if (first === "settings") {
    const section = second === "models" || second === "server" || second === "problems" ? second : store.get("omni.settings", "server") === "models" ? "models" : "server";
    return { tab: "settings", section, role: section === "models" && ROLE_ORDER.includes(third) ? third : null };
  }
  return { tab: ROLE_ORDER.includes(first) ? first : savedTab(), section: null, role: null };
}

export function pathFor({ tab, section, role }) {
  if (tab !== "settings") return `/${tab}`;
  return `/settings/${section ?? "server"}${section === "models" && role ? `/${role}` : ""}`;
}

export default function App() {
  const [route, setRoute] = useState(() => parsePath(typeof window === "undefined" ? "/" : window.location.pathname));
  const [lastRole, setLastRole] = useState(savedTab);
  const [moreCards, setMoreCards] = useState(false);
  const tab = route.tab;
  const [theme, setTheme] = useTheme();

  usePolling(loadModels, 30000, { immediate: true });

  // Pauses itself while the document is hidden.
  usePolling(pollRuntime, 4000);
  usePolling(pollDownloads, 2000);
  usePolling(pollExecs, 2000);

  useEffect(() => {
    const canonical = pathFor(route);
    if (window.location.pathname !== canonical) window.history.replaceState(null, "", canonical);
    if (route.tab !== "settings") {
      store.set("omni.tab", route.tab);
      setLastRole(route.tab);
    } else if (route.section) {
      store.set("omni.settings", route.section);
    }
    const onPop = () => setRoute(parsePath(window.location.pathname));
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  const go = (next) => {
    if (pathFor(next) !== window.location.pathname) window.history.pushState(null, "", pathFor(next));
    setRoute(next);
    if (next.tab !== "settings") {
      store.set("omni.tab", next.tab);
      setLastRole(next.tab);
    } else {
      store.set("omni.settings", next.section);
    }
  };

  const cardsRef = useRef(null);
  useEffect(() => {
    cardsRef.current?.querySelector('[aria-current="page"]')?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, [tab]);

  useEffect(() => {
    const row = cardsRef.current;
    if (!row) return undefined;
    const measure = () => setMoreCards(row.scrollTop + row.clientHeight < row.scrollHeight - 4);
    measure();
    row.addEventListener("scroll", measure, { passive: true });
    window.addEventListener("resize", measure);
    return () => {
      row.removeEventListener("scroll", measure);
      window.removeEventListener("resize", measure);
    };
  }, [tab === "settings"]);

  const choose = (id) => go(id === "settings"
    ? { tab: "settings", section: parsePath("/settings").section, role: null }
    : { tab: id, section: null, role: null });

  // A card whose default cannot start: open Settings at the Models section filtered to this role.
  const openSetup = (role) => go({ tab: "settings", section: "models", role });

  // A card whose runtime is down: the fix lives in the server controls, not in the card.
  const openServer = () => go({ tab: "settings", section: "server", role: null });

  const nextTheme = THEME_ORDER[(THEME_ORDER.indexOf(theme) + 1) % THEME_ORDER.length];
  const settingsOpen = tab === "settings";

  return (
    <div class={styles.app}>
      <header class={styles.header}>
        <div class={styles.brand}>
          <img src={`${import.meta.env?.BASE_URL ?? "/"}favicon.svg`} alt="" width="28" height="28" />
          <span class={styles.brandName}>OMNI API</span>
          <GatewayStatus />
        </div>
        <div class={styles.actions}>
          <SystemUsage />
          <LastRequest />
          <ui.IconButton
            icon={theme}
            label={`Theme: ${THEME_LABEL[theme]}. Switch to ${THEME_LABEL[nextTheme]}`}
            onClick={() => setTheme(nextTheme)}
          />
          <ui.IconButton
            icon="settings"
            label="Settings"
            active={settingsOpen}
            aria-current={settingsOpen ? "page" : undefined}
            onClick={() => choose("settings")}
          />
        </div>
      </header>
      <OllamaBanner />
      <div class={styles.body}>
        {!settingsOpen && (
          <nav ref={cardsRef} class={cx(styles.cards, moreCards && styles.more)} aria-label="Models">
            {ROLE_ORDER.map((role) => (
              <RoleModelCard key={role} density="mini" role={role} active={role === tab} onOpen={choose} onSetup={openSetup} onServer={openServer} />
            ))}
          </nav>
        )}
        <div class={styles.content}>
          <main class={styles.main}>
            {/* Every page stays mounted so an in-flight request survives a tab
                switch; the inactive ones just hide. */}
            {ROLE_ORDER.map((role) => {
              const RolePage = COMPONENTS[role];
              return (
                <div key={role} class={role === tab && !settingsOpen ? styles.page : styles.pageHidden} aria-hidden={role === tab && !settingsOpen ? undefined : true}>
                  <PageActive.Provider value={role === tab && !settingsOpen}>
                    <RolePage active={role === tab && !settingsOpen} />
                  </PageActive.Provider>
                </div>
              );
            })}
            {settingsOpen && <Settings key="settings" section={route.section} role={route.role} backTo={lastRole} onNavigate={go} onBack={() => choose(lastRole)} />}
          </main>
        </div>
      </div>
      <ConfirmHost />
      <Toasts />
    </div>
  );
}
