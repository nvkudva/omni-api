import { useEffect, useState } from "preact/hooks";
import { getJson } from "../api/client.js";
import { modelStatus, serverInfo } from "../api/endpoints.js";
import { loadModels } from "../api/models.js";
import { pollRuntime } from "../api/roles.js";
import { pollHealth } from "../api/health.js";
import * as ui from "./ui.jsx";
import { RoleModelCard } from "./RoleModelCard.jsx";
import { FindAndInstall } from "./FindAndInstall.jsx";
import styles from "./FindAndInstall.module.css";

/**
 * One role's model settings card in a popup. Opened from the role tab's
 * header button; the same detail card the Models page shows, without leaving
 * the tab. Fetches its own catalog entry on open.
 */
export function ModelSettingsPopup({ role, onClose }) {
  const [status, setStatus] = useState(null);
  const [tunables, setTunables] = useState(null);
  const [pins, setPins] = useState([]);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [addRole, setAddRole] = useState(null);

  const refresh = async () => {
    try {
      const [setupStatus, info] = await Promise.all([modelStatus(), serverInfo().catch(() => null)]);
      setStatus(setupStatus);
      setTunables(info?.tunables ?? null);
      setError("");
    } catch (failure) {
      setError(failure.message);
    }
    pollHealth();
    loadModels();
    try {
      setPins((await getJson("/admin/pins")).pinned ?? []);
    } catch {
      setPins([]);
    }
  };

  useEffect(() => {
    if (!role) return undefined;
    refresh();
    const opener = document.activeElement;
    const onKey = (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose?.();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("keydown", onKey); opener?.focus?.(); };
  }, [role]);

  if (!role) return null;

  const changed = (message) => {
    setNotice(message);
    refresh();
    pollRuntime();
  };

  const entry = status?.roles[role];

  return (
    <div
      class={styles.backdrop}
      onClick={(event) => { if (event.target === event.currentTarget) onClose?.(); }}
    >
      <div class={styles.dialog} role="dialog" aria-modal="true" aria-label={`${role} model settings`}>
        <div class={styles.body}>
          {error && <ui.ErrorText>{error}</ui.ErrorText>}
          {notice && <ui.Notice>{notice}</ui.Notice>}
          {!status && !error && <ui.Empty><ui.Spinner /> loading model settings…</ui.Empty>}
          {status && !entry && <ui.ErrorText>no model settings for role “{role}”.</ui.ErrorText>}
          {entry && (
            <RoleModelCard
              density="detail"
              role={role}
              entry={entry}
              pinned={pins.includes(role)}
              tunables={tunables}
              onChanged={changed}
              onSaved={refresh}
              onAdd={setAddRole}
            />
          )}
        </div>
        <footer class={styles.foot}>
          <ui.Button small onClick={() => onClose?.()}>Done</ui.Button>
        </footer>
      </div>
      {addRole && <FindAndInstall key={addRole} role={addRole} onClose={() => setAddRole(null)} onInstalled={() => changed("")} />}
    </div>
  );
}
