import { useEffect, useState } from "preact/hooks";
import { ROLE_META } from "../api/roles.js";
import { execs, execResult } from "../api/execs.js";
import * as ui from "./ui.jsx";
import { cx } from "../lib/cx.js";
import { size } from "../lib/format.js";
import styles from "./ActivityStrips.module.css";

/** The `role` prop keeps one role's entries (a card's own strip); `exceptRole` leaves one out (the page-wide strip). */
function roleFilter({ role, exceptRole }) {
  return (entry) => (!role || entry.role === role) && entry.role !== exceptRole;
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
export function ExecStrip({ role, exceptRole }) {
  const matchesRole = roleFilter({ role, exceptRole });
  const [openId, setOpenId] = useState(null);
  const { running: allRunning = [], recent: allRecent = [] } = execs.value;
  const running = allRunning.filter(matchesRole);
  const recent = allRecent.filter(matchesRole);
  const entries = [...running, ...recent.filter((entry) => entry.id !== openId)].slice(0, 8);
  const open = recent.find((entry) => entry.id === openId);
  if (!entries.length && !open) return null;
  const word = (entry) => entry.error ? "failed"
    : entry.finished_at ? `done · ${size(entry.size)}` : `running · ${size(entry.bytes)}`;
  const pageWide = !role || Boolean(exceptRole);
  return (
    <div class={cx(styles.downloads, pageWide && styles.global)} aria-label="Recent requests">
      {pageWide && <span class={styles.stripLabel}>Recent requests</span>}
      <span class={styles.srOnly} role="status">{running.length ? `${running.length} running` : ""}</span>
      {entries.map((entry) => (
        <div key={entry.id} class={styles.download}>
          <span class={styles.downloadLabel} title={`${entry.id} · ${entry.error ?? entry.path}`}>
            <strong>{ROLE_META[entry.role]?.label ?? entry.role}</strong> · {entry.path} · {word(entry)}
            {entry.error ? ` · ${entry.error}` : ""}
          </span>
          {entry.finished_at
            ? <ui.Button small class={styles.execButton} title="Show the response this request returned" onClick={() => setOpenId(openId === entry.id ? null : entry.id)}>View</ui.Button>
            : <ui.Spinner />}
        </div>
      ))}
      {open && (
        <div key={open.id} class={styles.download}>
          <span class={styles.downloadLabel} title={open.id}>
            <strong>{ROLE_META[open.role]?.label ?? open.role}</strong> · {open.path} · {word(open)}
          </span>
          <ui.Button small class={styles.execButton} onClick={() => setOpenId(null)}>Close</ui.Button>
        </div>
      )}
      {open && <ExecResult execId={open.id} />}
    </div>
  );
}
