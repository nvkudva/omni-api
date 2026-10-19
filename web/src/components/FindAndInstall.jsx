import { useEffect, useRef, useState } from "preact/hooks";
import { Icon } from "./Icon.jsx";
import { OLLAMA_ROLES, ROLE_META, downloadFor, install, installs } from "../api/roles.js";
import { checkModel, searchModels } from "../api/endpoints.js";
import { gb, size } from "../lib/format.js";
import * as ui from "./ui.jsx";
import styles from "./FindAndInstall.module.css";
import tabStyles from "../tabs/tabs.module.css";

/**
 * What each role's runtime can take, as UI guidance. The gateway enforces the
 * real rule (`SEARCH_FILTERS` in `registry/roles.py`) on search and check;
 * this table only drives wording — placeholder, hint, example, family chip —
 * so a new role adds one entry here and no new JSX branch.
 */
export const MODEL_CRITERIA = {
  chat: { kind: "GGUF · text generation", family: null, accepts: "Ollama: a library name (qwen2.5vl:7b) or any GGUF repo on Hugging Face as hf.co/user/repo-GGUF:Q4_K_M.", example: "hf.co/user/repo-GGUF:Q4_K_M" },
  decide: { kind: "GGUF · text generation", family: null, accepts: "Ollama: a library name (qwen2.5vl:7b) or any GGUF repo on Hugging Face as hf.co/user/repo-GGUF:Q4_K_M.", example: "hf.co/user/repo-GGUF:Q4_K_M" },
  embed: { kind: "GGUF · feature extraction", family: null, accepts: "Ollama: a library name (qwen2.5vl:7b) or any GGUF repo on Hugging Face as hf.co/user/repo-GGUF:Q4_K_M.", example: "hf.co/user/repo-GGUF:Q4_K_M" },
  stt: { kind: "MLX · Parakeet speech-to-text", family: "parakeet", accepts: "MLX Parakeet repo id or Hugging Face page URL, e.g. mlx-community/parakeet-tdt-0.6b-v3. Downloads to the HF cache.", example: "mlx-community/parakeet-tdt-0.6b-v3" },
  tts: { kind: "MLX · Kokoro text-to-speech", family: "kokoro", accepts: "MLX Kokoro repo id or Hugging Face page URL, e.g. mlx-community/Kokoro-82M-bf16. Downloads to the HF cache.", example: "mlx-community/Kokoro-82M-bf16" },
  image: { kind: "Diffusers · Qwen-Image-2", family: "qwen-image-2", accepts: "Qwen-Image-2 repo id or Hugging Face page URL, e.g. Qwen/Qwen-Image-2.1. Downloads to the HF cache.", example: "Qwen/Qwen-Image-2.1" },
  video: { kind: "MLX · LTX video", family: "ltx", accepts: "MLX LTX repo id or Hugging Face page URL, e.g. dgrauet/ltx-2.5-mlx-q4. Downloads to the HF cache.", example: "dgrauet/ltx-2.5-mlx-q4" },
};

const FALLBACK_CRITERIA = {
  ollama: MODEL_CRITERIA.chat,
  other: { kind: "Hugging Face repo", family: null, accepts: "Hugging Face repo id or page URL, e.g. mlx-community/whisper-large-v3-turbo. Downloads to the HF cache.", example: "mlx-community/whisper-large-v3-turbo" },
};

/** Criteria for a role; unknown roles fall back by runner, never by copying a sibling role. */
export function criteriaFor(role, runner) {
  if (MODEL_CRITERIA[role]) return MODEL_CRITERIA[role];
  if (OLLAMA_ROLES.has(role) || runner === "ollama") return FALLBACK_CRITERIA.ollama;
  return FALLBACK_CRITERIA.other;
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
      <ui.Meter fraction={progress.fraction} />
      <ui.Meta>{progress.status}</ui.Meta>
      {progress.warnings?.length > 0 && <ui.Notice>{progress.warnings.join(" ")}</ui.Notice>}
      {progress.error && <ui.ErrorText>{progress.error}</ui.ErrorText>}
    </>
  );
}

/** Find a model on Hugging Face that this role's runtime can load. */
export function Search({ role, onPick, inputRef }) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const criteria = criteriaFor(role);

  const search = async () => {
    setBusy(true);
    setError("");
    try {
      const params = new URLSearchParams({ role, q: query.trim() });
      setResults((await searchModels(params)).results);
    } catch (failure) {
      setError(failure.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div class={styles.line}>
        <input
          ref={inputRef}
          placeholder="Search Hugging Face (blank: most downloaded)"
          aria-label={`Search Hugging Face for ${ROLE_META[role]?.label ?? role} models`}
          value={query}
          onInput={(event) => setQuery(event.currentTarget.value)}
          onKeyDown={(event) => event.key === "Enter" && search()}
        />
        <ui.Button small variant="primary" disabled={busy} onClick={search}>{busy ? "Searching…" : "Search"}</ui.Button>
      </div>
      {error && <ui.ErrorText>{error}</ui.ErrorText>}
      {results && (results.length
        ? (
          <table>
            <thead><tr><th>Model</th><th>Downloads</th><th>Likes</th><th>Size</th><th /></tr></thead>
            <tbody>
              {results.map((result) => (
                <tr key={result.id}>
                  <td><a class={tabStyles.link} href={`https://huggingface.co/${result.id}`} target="_blank" rel="noreferrer">{result.id}</a></td>
                  <td class={tabStyles.numeric}>{(result.downloads ?? 0).toLocaleString()}</td>
                  <td class={tabStyles.numeric}>{(result.likes ?? 0).toLocaleString()}</td>
                  <td class={tabStyles.numeric}>{result.size_bytes ? `≈ ${size(result.size_bytes)}` : "—"}</td>
                  <td class={tabStyles.actionCell}><ui.Button small onClick={() => onPick(result.name)}>Select</ui.Button></td>
                </tr>
              ))}
            </tbody>
          </table>
        )
        : <ui.Empty>No supported {criteria.kind} models match.</ui.Empty>)}
    </>
  );
}

/** Download a model by id or page URL, after checking the role's runtime can run it. */
export function Install({ role, runner, initial, onInstalled }) {
  const [model, setModel] = useState(initial ?? "");
  const [check, setCheck] = useState(null);
  const [checking, setChecking] = useState(false);
  const busy = Boolean(installs.value[role]?.busy);
  const criteria = criteriaFor(role, runner);

  useEffect(() => {
    if (initial) {
      setModel(initial);
      setCheck(null);
    }
  }, [initial]);

  const verify = async () => {
    const name = model.trim();
    if (!name) return null;
    setChecking(true);
    let result;
    try {
      result = await checkModel(new URLSearchParams({ role, model: name }));
    } catch (failure) {
      result = { supported: false, reason: failure.message, installed: false, size_bytes: null, blockers: [] };
    } finally {
      setChecking(false);
    }
    setCheck(result);
    return result;
  };

  const download = async () => {
    const verdict = check ?? (await verify());
    if (!verdict?.supported || verdict.installed) return;
    if (await install(role, model.trim())) {
      setCheck({ ...verdict, installed: true });
      onInstalled();
    }
  };

  return (
    <>
      <div class={styles.line}>
        <input
          placeholder="repo id or huggingface.co page URL"
          aria-label="Model repo id or Hugging Face page URL"
          title={criteria.accepts}
          value={model}
          onInput={(event) => { setModel(event.currentTarget.value); setCheck(null); }}
        />
        <ui.Button small disabled={checking || busy || !model.trim()} onClick={verify}>{checking ? "Checking…" : "Check"}</ui.Button>
        <ui.Button
          small
          variant="primary"
          disabled={checking || busy || !model.trim() || check?.supported === false || check?.installed}
          onClick={download}
        >
          {busy ? "Installing…" : "Install"}
        </ui.Button>
      </div>
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
    </>
  );
}

/**
 * The one Find & Install popup, shared by every role. Open it with a role;
 * criteria, search filter and check all follow that role. `onInstalled`
 * refreshes the caller once a download lands.
 */
export function FindAndInstall({ role, onClose, onInstalled }) {
  const [pick, setPick] = useState(null);
  const searchRef = useRef(null);
  const meta = role ? ROLE_META[role] : null;
  const criteria = role ? criteriaFor(role) : null;

  useEffect(() => {
    if (!role) return undefined;
    const opener = document.activeElement;
    setPick(null);
    searchRef.current?.focus();
    const onKey = (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose?.();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("keydown", onKey); opener?.focus?.(); };
  }, [role]);

  if (!role || !meta || !criteria) return null;

  return (
    <div
      class={styles.backdrop}
      onClick={(event) => { if (event.target === event.currentTarget) onClose?.(); }}
    >
      <div class={styles.dialog} role="dialog" aria-modal="true" aria-label={`Find and install a ${meta.label} model`}>
        <header class={styles.head} style={{ background: `color-mix(in srgb, ${meta.color} 13%, var(--surface))` }}>
          <span class={styles.badge} style={{ background: meta.color }}><Icon name={role} /></span>
          <div class={styles.headText}>
            <h2 class={styles.title}>Find &amp; install — {meta.label}</h2>
            <span class={styles.headMeta}>{meta.endpoint}</span>
          </div>
          <ui.IconButton icon="x" label="Close" onClick={() => onClose?.()} />
        </header>
        <div class={styles.body}>
          <div class={styles.criteria}>
            <span class={styles.chip}>{criteria.kind}</span>
            {criteria.family && <span class={styles.chip}>name contains “{criteria.family}”</span>}
            <span class={styles.criteriaText}>{criteria.accepts}</span>
          </div>
          <Search role={role} onPick={setPick} inputRef={searchRef} />
          <h3 class={styles.subhead}>Or install by id</h3>
          <Install role={role} runner={meta.runner} initial={pick} onInstalled={() => onInstalled?.()} />
          <InstallProgress role={role} />
        </div>
        <footer class={styles.foot}>
          <ui.Button small onClick={() => onClose?.()}>Done</ui.Button>
        </footer>
      </div>
    </div>
  );
}
