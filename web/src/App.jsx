import { useEffect, useRef, useState } from "preact/hooks";
import { loadModels } from "./api/models.js";
import { downloads, installs, pollDownloads, pollRuntime, ROLE_META, ROLE_ORDER } from "./api/roles.js";
import { execs, execResult, pollExecs } from "./api/execs.js";
import { Meter, Spinner } from "./components/ui.jsx";
import { size } from "./lib/format.js";
import { PageActive } from "./hooks/usePageActive.js";
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
    const section = second === "models" || second === "server" ? second : store.get("omni.settings", "server") === "models" ? "models" : "server";
    return { tab: "settings", section, role: section === "models" && ROLE_ORDER.includes(third) ? third : null };
  }
  return { tab: ROLE_ORDER.includes(first) ? first : savedTab(), section: null, role: null };
}

export function pathFor({ tab, section, role }) {
  if (tab !== "settings") return `/${tab}`;
  return `/settings/${section ?? "server"}${section === "models" && role ? `/${role}` : ""}`;
}

/** Every model download in flight, on every tab: installs, first-setup loads and keep-warm pins. */
function DownloadStrip() {
  const merged = { ...downloads.value };
  for (const [role, progress] of Object.entries(installs.value)) {
    if (progress?.busy) merged[role] = progress;
  }
  const entries = Object.values(merged);
  if (!entries.length) return null;
  return (
    <div class={styles.downloads} role="status" aria-label="Model downloads in progress">
      {entries.map((entry) => (
        <div key={entry.model} class={styles.download}>
          <span class={styles.downloadLabel} title={entry.status}>
            <strong>{ROLE_META[entry.role]?.label ?? entry.role}</strong> · {entry.status}
          </span>
          <Meter fraction={entry.fraction} />
        </div>
      ))}
    </div>
  );
}

/** The picture inside an images API response (`data[0].b64_json`), or "" when there is none. */
function generatedImageUrl(text) {
  try {
    const b64 = JSON.parse(text)?.data?.[0]?.b64_json;
    return typeof b64 === "string" ? `data:image/png;base64,${b64}` : "";
  } catch {
    return "";
  }
}

/** A retained result, rendered by what it is: JSON/text as code, images shown, audio playable. */
function ExecResult({ execId }) {
  const [state, setState] = useState({ loading: true, kind: "", url: "", text: "", error: "" });
  useEffect(() => {
    let alive = true;
    let url = "";
    execResult(execId).then(async ({ blob, contentType: type }) => {
      if (!alive) return;
      if (type.startsWith("image/") || type.startsWith("audio/")) {
        url = URL.createObjectURL(blob);
        setState({ loading: false, kind: type.split("/")[0], url, text: "", error: "" });
        return;
      }
      const text = await blob.text();
      const imageUrl = type.includes("json") ? generatedImageUrl(text) : "";
      if (imageUrl) setState({ loading: false, kind: "image", url: imageUrl, text: "", error: "" });
      else setState({ loading: false, kind: "text", url: "", text: text.slice(0, 20000), error: "" });
    }).catch((failure) => {
      if (alive) setState({ loading: false, kind: "", url: "", text: "", error: failure.message });
    });
    return () => { alive = false; if (url) URL.revokeObjectURL(url); };
  }, [execId]);
  if (state.loading) return <span class={styles.downloadLabel}>reading result…</span>;
  if (state.error) return <span class={styles.downloadLabel}>result unreadable: {state.error}</span>;
  if (state.kind === "image") return <img src={state.url} alt="retained result" class={styles.execImage} />;
  if (state.kind === "audio") return <audio controls src={state.url} class={styles.execAudio} />;
  return <pre class={styles.execText}>{state.text}</pre>;
}

/** Every retained inference execution, on every tab: running ones with live bytes, recent ones
 *  with their results. Work the browser never saw finish is still here after a reopen. */
function ExecStrip() {
  const [openId, setOpenId] = useState(null);
  const { running = [], recent = [] } = execs.value;
  const entries = [...running, ...recent.filter((entry) => entry.id !== openId)].slice(0, 8);
  const open = recent.find((entry) => entry.id === openId);
  if (!entries.length && !open) return null;
  const word = (entry) => entry.error ? "failed"
    : entry.finished_at ? `done · ${size(entry.size)}` : `running · ${size(entry.bytes)}`;
  return (
    <div class={styles.downloads} aria-label="Recent requests">
      <span class={styles.srOnly} role="status">{running.length ? `${running.length} running` : ""}</span>
      {entries.map((entry) => (
        <div key={entry.id} class={styles.download}>
          <span class={styles.downloadLabel} title={`${entry.id} · ${entry.error ?? entry.path}`}>
            <strong>{ROLE_META[entry.role]?.label ?? entry.role}</strong> · {entry.path} · {word(entry)}
            {entry.error ? ` · ${entry.error}` : ""}
          </span>
          {entry.finished_at
            ? <button type="button" class={styles.execButton} onClick={() => setOpenId(openId === entry.id ? null : entry.id)}>View</button>
            : <Spinner />}
        </div>
      ))}
      {open && (
        <div key={open.id} class={styles.download}>
          <span class={styles.downloadLabel} title={open.id}>
            <strong>{ROLE_META[open.role]?.label ?? open.role}</strong> · {open.path} · {word(open)}
          </span>
          <button type="button" class={styles.execButton} onClick={() => setOpenId(null)}>Close</button>
        </div>
      )}
      {open && <ExecResult execId={open.id} />}
    </div>
  );
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
    const measure = () => setMoreCards(row.scrollLeft + row.clientWidth < row.scrollWidth - 4);
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
    <div class={styles.shell}>
      <header class={styles.header}>
        <div class={styles.brand}>
          <img src={`${import.meta.env?.BASE_URL ?? "/"}favicon.svg`} alt="" width="28" height="28" />
          <span class={styles.brandName}>OMNI API</span>
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
      {!settingsOpen && (
        <nav ref={cardsRef} class={moreCards ? `${styles.cards} ${styles.cardsMore}` : styles.cards} aria-label="Models">
          {ROLE_ORDER.map((role) => (
            <RoleModelCard key={role} density="mini" role={role} active={role === tab} onOpen={choose} onSetup={openSetup} onServer={openServer} />
          ))}
        </nav>
      )}
      <DownloadStrip />
      <ExecStrip />
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
      <ConfirmHost />
    </div>
  );
}
