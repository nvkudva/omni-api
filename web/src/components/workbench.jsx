import { useState } from "preact/hooks";
import { cx } from "../lib/cx.js";
import { ROLE_META } from "../api/roles.js";
import * as ui from "./ui.jsx";
import { ModelSettingsPopup } from "./ModelSettings.jsx";
import { ExecStrip } from "./ActivityStrips.jsx";
import styles from "./workbench.module.css";

// The recent-requests strip now sits below the page's log card, not above the page.
// It stays hidden until it earns that place.
const SHOW_ACTIVITY = false;

// The shared request/response workbench: everything the model pages need to lay a
// request out on the left and a log of turns on the right. Each role page's
// endpoint rides in its request panel's header; the role's model and status live
// on the role card above the page, so a role page does not repeat them. The
// optional `readout` is for overview pages (Models) that have no role card.

/**
 * Which turn is expanded and which view each turn is showing. `open` is null
 * until a turn is picked, so callers can default to the newest turn.
 */
export function useTurnState() {
  const [open, setOpen] = useState(null);
  const [views, setViews] = useState({});
  return {
    open,
    toggle: (id) => setOpen((current) => (current === id ? null : id)),
    viewOf: (id) => views[id] ?? "ui",
    setViewOf: (id, view) => setViews((current) => ({ ...current, [id]: view })),
  };
}

export function Workbench({ title, subtitle, readout, single, modelRole, headerTools, class: pageClass, children }) {
  const [settingsOpen, setSettingsOpen] = useState(false);
  const roleLabel = (modelRole && ROLE_META[modelRole]?.label) ?? modelRole;
  return (
    <div class={cx(styles.workbench, pageClass)}>
      <header class={styles.bar}>
        <div class={styles.barInner}>
          <div class={styles.barHead}>
            <div class={styles.titleRow}>
              <h1 class={styles.title}>{title}</h1>
              {(headerTools || modelRole) && (
                <div class={styles.barTools}>
                  {headerTools}
                  {modelRole && (
                    <ui.IconButton
                      icon="settings"
                      label={roleLabel ? `Open ${roleLabel} model settings` : "Open model settings"}
                      title={roleLabel ? `${roleLabel} model settings` : "Model settings"}
                      onClick={() => setSettingsOpen(true)}
                    />
                  )}
                </div>
              )}
            </div>
            {subtitle && <p class={styles.subtitle}>{subtitle}</p>}
          </div>
          {readout && <Readout fields={readout} />}
        </div>
      </header>
      <main class={cx(styles.main, single && styles.single)}>{children}</main>
      {SHOW_ACTIVITY && modelRole && <ExecStrip role={modelRole} exceptRole="embed" />}
      {settingsOpen && modelRole && <ModelSettingsPopup role={modelRole} onClose={() => setSettingsOpen(false)} />}
    </div>
  );
}

/** `fields`: [{ label, value, title, node }]. `node` overrides `value` for controls
    (a model select, say); values fold to one line with an ellipsis. */
export function Readout({ fields }) {
  const shown = (fields ?? []).filter(Boolean);
  if (!shown.length) return null;
  return (
    <dl class={cx(ui.css.facts, styles.readout)}>
      {shown.map((field) => (
        <div key={field.label}>
          <dt>{field.label}</dt>
          <dd title={field.title ?? (typeof field.value === "string" ? field.value : undefined)}>
            {field.node ?? field.value ?? "—"}
          </dd>
        </div>
      ))}
    </dl>
  );
}

/** `endpoint` is the page's wire path, shown as a quiet chip beside the title. */
export function Panel({ title, meta, endpoint, tools, foot, children }) {
  return (
    <section class={styles.panel}>
      {(title || meta || tools) && (
        <header class={styles.head}>
          <div class={ui.css.titleGroup}>
            {title && <h2 class={styles.title}>{title}</h2>}
            {endpoint && <code class={styles.endpoint}>{endpoint}</code>}
            {meta && <ui.Meta>{meta}</ui.Meta>}
          </div>
          {tools && <div class={styles.tools}>{tools}</div>}
        </header>
      )}
      <div class={styles.body}>{children}</div>
      {foot && <div class={styles.foot}>{foot}</div>}
    </section>
  );
}

/** The UI / JSON / cURL switch for the request panel. */
export function UiJson({ view, onChange, label = "view" }) {
  return (
    <ui.Segmented
      label={label}
      options={[
        { value: "ui", label: "UI" },
        { value: "json", label: "JSON" },
        { value: "curl", label: "cURL" },
      ]}
      value={view}
      onChange={onChange}
    />
  );
}

/** The UI / JSON switch for a turn header (turns have no curl of their own). */
export function TurnView({ view, onChange, label = "view" }) {
  return (
    <ui.Segmented
      compact
      label={label}
      options={[{ value: "ui", label: "UI" }, { value: "json", label: "JSON" }]}
      value={view}
      onChange={onChange}
    />
  );
}

/**
 * One request/response pair.
 *
 * `wire` is the JSON pair shown when the turn is switched to JSON; `summary` is
 * the one-line prose that stays visible when the turn is collapsed; `children`
 * is the rendered answer.
 */
export function Turn({
  label,
  at,
  model,
  meta,
  status,
  warn,
  view = "ui",
  onView,
  open = true,
  onToggle,
  summary,
  wire,
  children,
}) {
  const time = at ? new Date(at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : null;
  const collapsible = typeof onToggle === "function";
  const head = (
    <header
      class={cx(styles.head, !collapsible && styles.static)}
      role={collapsible ? "button" : undefined}
      tabIndex={collapsible ? 0 : undefined}
      aria-expanded={collapsible ? open : undefined}
      onClick={(event) => {
        if (!collapsible || event.target.closest("button")) return;
        onToggle();
      }}
      onKeyDown={(event) => {
        if (!collapsible || (event.key !== "Enter" && event.key !== " ")) return;
        if (event.target.closest("button")) return;
        event.preventDefault();
        onToggle();
      }}
    >
      {collapsible && <span class={styles.caret} aria-hidden="true">{open ? "▾" : "▸"}</span>}
      <span class={styles.label}>{label}</span>
      {time && <span>· {time}</span>}
      <span class={styles.meta}>
        {warn && <span class={styles.warn}>{warn}</span>}
        {status && <span>{status}</span>}
        {meta}
        {onView && <TurnView view={view} onChange={onView} label={`view for ${label}`} />}
      </span>
    </header>
  );

  let body;
  if (!open) {
    body = summary ? <div class={styles.summary}>{summary}</div> : null;
  } else if (view === "json") {
    body = <ui.CodeBlock code={JSON.stringify(wire, null, 2)} language="json" label={`copy ${label} JSON`} />;
  } else {
    body = (
      <>
        {summary && <div class={styles.summary}>{summary}</div>}
        <div class={styles.body}>{children}</div>
      </>
    );
  }

  return (
    <article class={cx(styles.turn, open && styles.open)}>
      {head}
      {body}
    </article>
  );
}

/** A turn that is still running. */
export function Pending({ label = "Request", at, note, children }) {
  return (
    <article class={cx(styles.turn, styles.open)}>
      <header class={cx(styles.head, styles.static)}>
        <span class={styles.label}>{label}</span>
        {at && <span>· {new Date(at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>}
      </header>
      <div class={styles.pending}>
        {note && <span>{note}</span>}
        <div class={styles.progress} role="progressbar" aria-label={note ?? "working"} />
        {children}
      </div>
    </article>
  );
}

export function TurnError({ children }) {
  return <div class={styles.error}><ui.ErrorText>{children}</ui.ErrorText></div>;
}

const BAR_PERCENT = (value) => `${(value * 100 >= 9.95 ? (value * 100).toFixed(0) : (value * 100).toFixed(1))}%`;

/**
 * One answer as a distribution: a single fill on a neutral track, rank carried
 * by ink weight. `rows` is [{ label, value }] where value is 0–1.
 */
export function Distribution({ name, type, instruction, answer, rows, foot, format = BAR_PERCENT }) {
  const top = rows.reduce((best, row, index) => (row.value > rows[best].value ? index : best), 0);
  return (
    <section class={styles.distribution}>
      <div class={styles.question}>
        <span class={styles.instruction}>{instruction || name}</span>
        <span class={styles.answer}>{answer}</span>
      </div>
      <header class={styles.head}>
        <h3>{name}</h3>
        {type && <span class={styles.type}>{type}</span>}
      </header>
      <ol class={styles.bars}>
        {rows.map((row, index) => (
          <li key={row.label} class={cx(styles.bar, index === top && styles.top)} title={`${row.label} — ${row.value}`}>
            <span class={styles.label}>{row.label}</span>
            <span class={styles.track}>
              <span class={styles.fill} style={{ width: `max(2px, ${Math.max(0, Math.min(1, row.value)) * 100}%)` }} />
            </span>
            <span class={styles.value}>{format(row.value, row)}</span>
          </li>
        ))}
      </ol>
      {foot && <footer class={styles.foot}>{foot}</footer>}
    </section>
  );
}

/**
 * The request panel body: the caller's form fields, an editable JSON pane when
 * the panel is switched to JSON, or a read-only tinted curl pane with a copy
 * button when it is switched to cURL.
 */
export function RequestBody({ view, jsonText, onJsonText, jsonError, curl, children }) {
  if (view === "curl") {
    return <ui.CodeBlock code={curl ?? ""} language="shell" label="copy curl command" />;
  }
  if (view !== "json") return children;
  return (
    <>
      <textarea
        class={styles.requestBody}
        spellcheck={false}
        aria-label="request JSON"
        value={jsonText}
        onInput={(event) => onJsonText(event.currentTarget.value)}
      />
      {jsonError && <ui.ErrorText>Not valid JSON — {jsonError}</ui.ErrorText>}
    </>
  );
}
