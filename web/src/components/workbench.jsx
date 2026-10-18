import { useState } from "preact/hooks";
import { cx } from "../lib/cx.js";
import * as ui from "./ui.jsx";
import styles from "./workbench.module.css";

// The shared request/response workbench: everything the model pages need to lay a
// request out on the left, a log of turns on the right, and a readout up top.

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

export function Workbench({ title, subtitle, readout, single, children }) {
  return (
    <div class={styles.shell}>
      <header class={styles.bar}>
        <div class={styles.barInner}>
          <div class={styles.barHead}>
            <h1 class={styles.title}>{title}</h1>
            {subtitle && <p class={styles.subtitle}>{subtitle}</p>}
          </div>
          {readout && <Readout fields={readout} />}
        </div>
      </header>
      <main class={cx(styles.main, single && styles.mainSingle)}>{children}</main>
    </div>
  );
}

/** `fields`: [{ label, value, title, node }]. `node` overrides `value` for controls
    (a model select, say); values fold to one line with an ellipsis. */
export function Readout({ fields }) {
  const shown = (fields ?? []).filter(Boolean);
  if (!shown.length) return null;
  return (
    <dl class={styles.readout}>
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

export function Panel({ title, meta, tools, flush, foot, children }) {
  return (
    <section class={styles.panel}>
      {(title || meta || tools) && (
        <header class={styles.panelHead}>
          <div class={styles.panelTitleGroup}>
            {title && <h2 class={styles.panelTitle}>{title}</h2>}
            {meta && <span class={styles.panelMeta}>{meta}</span>}
          </div>
          {tools && <div class={styles.panelTools}>{tools}</div>}
        </header>
      )}
      <div class={cx(styles.panelBody, flush && styles.panelBodyFlush)}>{children}</div>
      {foot && <div class={styles.panelFoot}>{foot}</div>}
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
      class={cx(styles.turnHead, !collapsible && styles.turnStatic)}
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
      {collapsible && <span class={styles.turnCaret} aria-hidden="true">{open ? "▾" : "▸"}</span>}
      <span class={styles.turnLabel}>{label}</span>
      {time && <span>· {time}</span>}
      <span class={styles.turnMeta}>
        {warn && <span class={styles.turnWarn}>{warn}</span>}
        {status && <span>{status}</span>}
        {meta}
        {onView && <TurnView view={view} onChange={onView} label={`view for ${label}`} />}
      </span>
    </header>
  );

  let body;
  if (!open) {
    body = summary ? <div class={styles.turnSummary}>{summary}</div> : null;
  } else if (view === "json") {
    body = <ui.CodeBlock code={JSON.stringify(wire, null, 2)} language="json" label={`copy ${label} JSON`} />;
  } else {
    body = (
      <>
        {summary && <div class={styles.turnSummary}>{summary}</div>}
        <div class={styles.turnBody}>{children}</div>
      </>
    );
  }

  return (
    <article class={cx(styles.turn, open && styles.turnOpen)}>
      {head}
      {body}
    </article>
  );
}

/** A turn that is still running. */
export function Pending({ label = "Request", at, note, children }) {
  return (
    <article class={cx(styles.turn, styles.turnOpen)}>
      <header class={cx(styles.turnHead, styles.turnStatic)}>
        <span class={styles.turnLabel}>{label}</span>
        {at && <span>· {new Date(at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>}
      </header>
      <div class={styles.turnPending}>
        {note && <span>{note}</span>}
        <div class={styles.progress} role="progressbar" aria-label={note ?? "working"} />
        {children}
      </div>
    </article>
  );
}

export function TurnError({ children }) {
  return <div class={styles.turnError}><ui.ErrorText>{children}</ui.ErrorText></div>;
}

const BAR_PERCENT = (value) => `${(value * 100 >= 9.95 ? (value * 100).toFixed(0) : (value * 100).toFixed(1))}%`;

/**
 * One answer as a distribution: a single fill on a neutral track, rank carried
 * by ink weight. `rows` is [{ label, value }] where value is 0–1.
 */
export function Distribution({ name, type, instruction, answer, rows, foot, format = BAR_PERCENT }) {
  const top = rows.reduce((best, row, index) => (row.value > rows[best].value ? index : best), 0);
  return (
    <section class={styles.dist}>
      <div class={styles.distQ}>
        <span class={styles.distInstr}>{instruction || name}</span>
        <span class={styles.distAnswer}>{answer}</span>
      </div>
      <header class={styles.distHead}>
        <h3>{name}</h3>
        {type && <span class={styles.distType}>{type}</span>}
      </header>
      <ol class={styles.bars}>
        {rows.map((row, index) => (
          <li key={row.label} class={cx(styles.barRow, index === top && styles.barTop)} title={`${row.label} — ${row.value}`}>
            <span class={styles.barLabel}>{row.label}</span>
            <span class={styles.barTrack}>
              <span class={styles.barFill} style={{ width: `max(2px, ${Math.max(0, Math.min(1, row.value)) * 100}%)` }} />
            </span>
            <span class={styles.barValue}>{format(row.value, row)}</span>
          </li>
        ))}
      </ol>
      {foot && <footer class={styles.distFoot}>{foot}</footer>}
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
        class={styles.jsonPane}
        spellcheck={false}
        aria-label="request JSON"
        value={jsonText}
        onInput={(event) => onJsonText(event.currentTarget.value)}
      />
      {jsonError && <ui.ErrorText>Not valid JSON — {jsonError}</ui.ErrorText>}
    </>
  );
}
