import { useEffect, useState } from "preact/hooks";
import { getJson } from "../api/client.js";
import { modelStatus, serverInfo } from "../api/endpoints.js";
import { loadModels } from "../api/models.js";
import { pollRuntime } from "../api/roles.js";
import { pollHealth } from "../api/health.js";
import * as ui from "./ui.jsx";
import { RoleModelCard } from "./RoleModelCard.jsx";
import { FindAndInstall } from "./FindAndInstall.jsx";

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
    return undefined;
  }, [role]);

  if (!role) return null;

  const changed = (message) => {
    setNotice(message);
    refresh();
    pollRuntime();
  };

  const entry = status?.roles[role];

  return (
    <>
      <ui.Modal role="dialog" label={`${role} model settings`} onClose={onClose} class={ui.css.modalLarge}>
        <div class={ui.css.modalBody}>
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
        <footer class={ui.css.modalFoot}>
          <ui.Button small onClick={() => onClose?.()}>Done</ui.Button>
        </footer>
      </ui.Modal>
      {addRole && <FindAndInstall key={addRole} role={addRole} onClose={() => setAddRole(null)} onInstalled={() => changed("")} />}
    </>
  );
}
