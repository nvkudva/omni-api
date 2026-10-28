import { useRef, useState } from "preact/hooks";
import { Icon } from "./Icon.jsx";
import { ROLE_META, downloadFor, install, installs, specs, stopDownload } from "../api/roles.js";
import { checkModel, searchModels } from "../api/endpoints.js";
import { gb, size } from "../lib/format.js";
import * as ui from "./ui.jsx";
import styles from "./FindAndInstall.module.css";

// Wording for a role the server has not described yet (its first poll is still out).
const FALLBACK_CRITERIA = { kind: "Hugging Face repo", family: null, accepts: "Hugging Face repo id or page URL. Downloads to the HF cache.", example: "" };

/** What a role's runtime accepts, as the gateway describes it (`ROLE_SPECS` in `registry/roles.py`). */
export function criteriaFor(role) {
  return specs.value[role] ?? FALLBACK_CRITERIA;
}

export function GatedNotice({ model, acceptUrl, busy, onRetry, actionLabel = "Retry install", busyLabel = "Installing…" }) {
  const url = acceptUrl || `https://huggingface.co/${model}`;
  return (
    <>
      <ui.Notice tone="error">
        {model} needs its terms accepted before it can download.{" "}
        <a href={url} target="_blank" rel="noreferrer">Accept terms on Hugging Face</a>
        {" "}— sign in there, accept, then retry here. A Hugging Face token with access may also be required.
      </ui.Notice>
      <ui.Row>
        <ui.Button small variant="primary" disabled={busy} onClick={onRetry}>{busy ? busyLabel : actionLabel}</ui.Button>
      </ui.Row>
    </>
  );
}

/** Where a role's download stands: the same progress every view of that role shows. */
export function InstallProgress({ role }) {
  const progress = downloadFor(role);
  if (!progress || (!progress.busy && !progress.error && progress.fraction !== 1)) return null;
  if (progress.code === "gated") {
    return (
      <>
        <ui.Meter fraction={progress.fraction} />
        <ui.Meta>{progress.status}</ui.Meta>
        <GatedNotice model={progress.model} acceptUrl={progress.acceptUrl} busy={progress.busy} onRetry={() => install(role, progress.model)} />
      </>
    );
  }
  return (
    <>
      <div class={styles.progressRow}>
        <ui.Meter fraction={progress.fraction} />
        {progress.busy && <ui.Button small onClick={() => stopDownload(role)}>Stop download</ui.Button>}
      </div>
      <ui.Meta>{progress.status}</ui.Meta>
      {progress.warnings?.length > 0 && <ui.Notice>{progress.warnings.join(" ")}</ui.Notice>}
      {progress.error && <ui.ErrorText>{progress.error}</ui.ErrorText>}
    </>
  );
}

/**
 * One box for finding and installing: type to search Hugging Face, or paste a repo id or page
 * URL and install it (checked against the role's runtime first). Picking a result fills the box.
 */
export function Find({ role, onInstalled, inputRef }) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState(null);
  const [error, setError] = useState("");
  const [searching, setSearching] = useState(false);
  const [check, setCheck] = useState(null);
  const busy = Boolean(installs.value[role]?.busy);
  const criteria = criteriaFor(role);

  const edit = (value) => {
    setQuery(value);
    setCheck(null);
  };

  const search = async () => {
    setSearching(true);
    setError("");
    try {
      const params = new URLSearchParams({ role, q: query.trim() });
      setResults((await searchModels(params)).results);
    } catch (failure) {
      setError(failure.message);
    } finally {
      setSearching(false);
    }
  };

  const pick = (name) => {
    edit(name);
    setResults(null);
  };

  const verify = async () => {
    let result;
    try {
      result = await checkModel(new URLSearchParams({ role, model: query.trim() }));
    } catch (failure) {
      result = { supported: false, reason: failure.message, installed: false, size_bytes: null, blockers: [] };
    }
    setCheck(result);
    return result;
  };

  const download = async () => {
    const verdict = check ?? (await verify());
    if (!verdict?.supported || verdict.installed) return;
    if (await install(role, query.trim())) {
      setCheck({ ...verdict, installed: true });
      onInstalled();
    }
  };

  return (
    <>
      <div class={ui.css.inputRow}>
        <input
          ref={inputRef}
          placeholder="Search Hugging Face, or paste a repo id or page URL"
          aria-label={`Search Hugging Face for ${ROLE_META[role]?.label ?? role} models, or enter a repo id or page URL`}
          title={criteria.accepts}
          value={query}
          onInput={(event) => edit(event.currentTarget.value)}
          onKeyDown={(event) => event.key === "Enter" && search()}
        />
        <ui.Button small disabled={searching} onClick={search}>{searching ? "Searching…" : "Search"}</ui.Button>
        <ui.Button
          small
          variant="primary"
          disabled={busy || !query.trim() || check?.supported === false || check?.installed}
          onClick={download}
        >
          {busy ? "Installing…" : "Install"}
        </ui.Button>
      </div>
      {error && <ui.ErrorText>{error}</ui.ErrorText>}
      {check && !check.supported && <ui.ErrorText>{check.reason}</ui.ErrorText>}
      {check?.gated && installs.value[role]?.code !== "gated" && (
        <GatedNotice model={check.model} acceptUrl={check.accept_url} busy={busy} onRetry={download} />
      )}
      {check && check.supported && !check.gated && (
        <ui.Meta>
          {check.installed ? "already downloaded" : "compatible"}
          {check.size_bytes ? ` · ${gb(check.size_bytes)}` : ""}
          {check.blockers?.length ? ` · ${check.blockers.join(" ")}` : ""}
        </ui.Meta>
      )}
      {results && (results.length
        ? (
          <table>
            <thead><tr><th>Model</th><th>Downloads</th><th>Likes</th><th>Size</th><th /></tr></thead>
            <tbody>
              {results.map((result) => (
                <tr key={result.id}>
                  <td><a class={ui.css.link} href={`https://huggingface.co/${result.id}`} target="_blank" rel="noreferrer">{result.id}</a></td>
                  <td class={ui.css.numeric}>{(result.downloads ?? 0).toLocaleString()}</td>
                  <td class={ui.css.numeric}>{(result.likes ?? 0).toLocaleString()}</td>
                  <td class={ui.css.numeric}>{result.size_bytes ? `≈ ${size(result.size_bytes)}` : "—"}</td>
                  <td class={ui.css.actionCell}><ui.Button small onClick={() => pick(result.name)}>Select</ui.Button></td>
                </tr>
              ))}
            </tbody>
          </table>
        )
        : <ui.Empty>No supported {criteria.kind} models match.</ui.Empty>)}
    </>
  );
}

/**
 * The one Find & Install popup, shared by every role. Open it with a role;
 * criteria, search filter and check all follow that role. `onInstalled`
 * refreshes the caller once a download lands.
 */
export function FindAndInstall({ role, onClose, onInstalled }) {
  const searchRef = useRef(null);
  const meta = role ? ROLE_META[role] : null;
  const criteria = role ? criteriaFor(role) : null;

  if (!role || !meta || !criteria) return null;

  return (
    <ui.Modal
      role="dialog"
      label={`Find and install a ${meta.label} model`}
      onClose={onClose}
      initialFocus={searchRef}
      class={styles.findAndInstall}
    >
      <header class={styles.head} style={{ background: `color-mix(in srgb, ${meta.color} 13%, var(--card))` }}>
        <span class={ui.css.roleGlyph} style={{ "--role-color": meta.color }}><Icon name={role} /></span>
        <div class={styles.headText}>
          <h2 class={styles.title}>Find &amp; install — {meta.label}</h2>
          <span class={ui.css.endpoint}>{meta.endpoint}</span>
        </div>
        <ui.IconButton icon="x" label="Close" onClick={() => onClose?.()} />
      </header>
      <div class={ui.css.modalBody}>
        <div class={styles.criteria}>
          <span class={styles.chip}>{criteria.kind}</span>
          {criteria.family && <span class={styles.chip}>name contains “{criteria.family}”</span>}
          <span class={styles.criteriaText}>{criteria.accepts}</span>
        </div>
        <Find role={role} onInstalled={() => onInstalled?.()} inputRef={searchRef} />
        <InstallProgress role={role} />
      </div>
      <footer class={ui.css.modalFoot}>
        <ui.Button small onClick={() => onClose?.()}>Done</ui.Button>
      </footer>
    </ui.Modal>
  );
}
