import { cloneElement, isValidElement, toChildArray } from "preact";
import { useEffect, useId, useRef, useState } from "preact/hooks";
import { tintJson, tintShell } from "../lib/highlight.js";
import { useModal } from "../hooks/useModal.js";
import { Icon } from "./Icon.jsx";
import styles from "./ui.module.css";

// The module's class names, for the shared patterns other components reuse
// (stack, card, link, facts …). A component's own styles stay in its own module.
export { default as css } from "./ui.module.css";

const cx = (...names) => names.filter(Boolean).join(" ");

// `trailing` is intentionally rendered as a sibling of the <label> element, never
// inside it, so interactive controls (e.g. a Reset button) do not activate the field.
export function Field({ label, hint, trailing, class: extra, children }) {
  const id = useId();
  const list = toChildArray(children);
  // only native elements can take the label id; composites (Segmented etc.) label themselves
  const control = list.length === 1 && isValidElement(list[0]) && typeof list[0].type === "string" && !list[0].props?.id
    ? cloneElement(list[0], { id })
    : children;
  return (
    <div class={cx(styles.field, extra)}>
      {(label || trailing) && (
        <span class={styles.label}>
          {label && <label htmlFor={id}>{label}</label>}
          {trailing && <span class={styles.trailing}>{trailing}</span>}
        </span>
      )}
      {control}
      {hint && <span class={styles.hint}>{hint}</span>}
    </div>
  );
}

export function Grid({ children }) {
  return <div class={styles.grid}>{children}</div>;
}

export function Row({ end, children }) {
  return <div class={cx(styles.row, end && styles.end)}>{children}</div>;
}

export function Button({ variant = "outline", small, class: extra, ...props }) {
  return <button type="button" class={cx(styles.button, styles[variant], small && styles.small, extra)} {...props} />;
}

export function IconButton({ icon, label, danger, active, size = 16, class: extra, ...props }) {
  // `class` is merged, not spread: a caller's class must add to the icon styling
  // (e.g. align-self), never replace it and leave a bare native button.
  return (
    <button
      type="button"
      title={label}
      aria-label={props["aria-label"] ?? label}
      class={cx(styles.iconButton, danger && styles.danger, active && styles.on, extra)}
      {...props}
    >
      <Icon name={icon} size={size} />
    </button>
  );
}

export function Segmented({ options, value, onChange, label, compact, full }) {
  const move = (current, key) => {
    const index = options.findIndex((option) => option.value === current);
    const next = key === "ArrowRight" || key === "ArrowDown" ? index + 1
      : key === "ArrowLeft" || key === "ArrowUp" ? index - 1 : null;
    if (next === null) return;
    const target = options[(next + options.length) % options.length];
    onChange(target.value);
  };
  return (
    <div class={cx(styles.segmented, compact && styles.compact, full && styles.full)} role="radiogroup" aria-label={label}>
      {options.map((option) => (
        <button
          type="button"
          role="radio"
          key={option.value}
          tabIndex={option.value === value ? 0 : -1}
          aria-checked={option.value === value}
          class={cx(styles.segment, option.value === value && styles.on)}
          onClick={() => onChange(option.value)}
          onKeyDown={(event) => move(option.value, event.key)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

export function Toggle({ checked, onChange, children }) {
  return (
    <label class={styles.toggle}>
      <input type="checkbox" checked={checked} onChange={(event) => onChange(event.currentTarget.checked)} />
      <span>{children}</span>
    </label>
  );
}

/** shadcn Switch: `checked` slides the thumb; the click handler comes from the caller. */
export function Switch({ checked, class: extra, ...props }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      class={cx(styles.switch, checked && styles.on, extra)}
      {...props}
    />
  );
}

export function Code({ children }) {
  return <pre class={styles.code}>{children}</pre>;
}

const TOK_CLASS = {
  key: styles.tokKey,
  string: styles.tokString,
  number: styles.tokNumber,
  bool: styles.tokBool,
  null: styles.tokNull,
  flag: styles.tokFlag,
  comment: styles.tokComment,
};

/**
 * Read-only tinted code with a copy button: the request-pane curl view and
 * the turn JSON view. `language` is "json" or "shell".
 */
export function CodeBlock({ code, language = "json", label = "copy code" }) {
  const [state, setState] = useState("idle");
  const timer = useRef(null);
  useEffect(() => () => clearTimeout(timer.current), []);
  const copy = async () => {
    let next = "copied";
    try {
      await navigator.clipboard.writeText(code);
    } catch {
      next = "failed";
    }
    setState(next);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setState("idle"), 1200);
  };
  const runs = language === "shell" ? tintShell(code) : tintJson(code);
  return (
    <div class={styles.codeBlock}>
      <span class={styles.copy}>
        <IconButton icon="copy" label={state === "copied" ? "Copied" : state === "failed" ? "Copy failed" : label} onClick={copy} />
      </span>
      <pre class={styles.code} tabIndex={0}>
        {runs.map((run, index) => (
          run.tone && TOK_CLASS[run.tone]
            ? <span key={index} class={TOK_CLASS[run.tone]}>{run.text}</span>
            : run.text
        ))}
      </pre>
    </div>
  );
}

export function Meter({ fraction }) {
  const percent = Math.min(1, Math.max(0, fraction || 0)) * 100;
  return (
    <div class={styles.meter} role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(percent)}>
      <span class={styles.fill} style={{ width: `${percent}%` }} />
    </div>
  );
}

export function Notice({ tone = "warn", children }) {
  return <p role={tone === "error" ? "alert" : "status"} class={cx(styles.notice, styles[tone])}>{children}</p>;
}

export function ErrorText({ children }) {
  return <Notice tone="error">{children}</Notice>;
}

export function Empty({ children }) {
  return <p class={styles.empty}>{children}</p>;
}

export function Meta({ class: extra, children }) {
  return <span class={cx(styles.meta, extra)}>{children}</span>;
}

// A status word's colour. Anything unlisted reads as a problem, which is the safe default for a
// light someone reads at a glance; `idle` is the neutral one — nothing wrong, nothing in memory.
const DOT_TONE = {
  ready: "ok",
  external: "ok",
  loaded: "ok",
  starting: "busy",
  restarting: "busy",
  unknown: "busy",
  idle: "idle",
};

export function Dot({ status, tone }) {
  const key = tone ?? DOT_TONE[status] ?? "bad";
  return <span class={cx(styles.dot, styles[key])} />;
}

export function Spinner() {
  return <span class={styles.spinner} role="status" aria-label="working" />;
}

export function DropZone({ accept, onFile, children }) {
  const input = useRef(null);
  const [over, setOver] = useState(false);
  const pick = (file) => file && onFile(file);
  return (
    <div
      class={cx(styles.drop, over && styles.over)}
      role="button"
      tabIndex={0}
      onClick={() => input.current.click()}
      onKeyDown={(event) => { if (event.key !== "Enter" && event.key !== " ") return; event.preventDefault(); input.current.click(); }}
      onDragOver={(event) => { event.preventDefault(); setOver(true); }}
      onDragLeave={() => setOver(false)}
      onDrop={(event) => { event.preventDefault(); setOver(false); pick(event.dataTransfer.files[0]); }}
    >
      <input ref={input} type="file" accept={accept} hidden onChange={(event) => { pick(event.currentTarget.files[0]); event.currentTarget.value = ""; }} />
      {children}
    </div>
  );
}

/**
 * The one dialog shell: dimmed backdrop, centred surface, Escape and Tab handled
 * by `useModal`. `class` sizes the surface; the content nests under the caller's
 * own component class.
 */
export function Modal({ role = "dialog", label, class: extra, onClose, initialFocus, children }) {
  const surface = useRef(null);
  useModal(surface, onClose, initialFocus);
  return (
    <div class={styles.modal} onClick={(event) => { if (event.target === event.currentTarget) onClose?.(); }}>
      <div ref={surface} role={role} aria-modal="true" aria-label={label} class={cx(styles.surface, extra)}>
        {children}
      </div>
    </div>
  );
}
