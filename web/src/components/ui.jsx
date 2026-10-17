import { cloneElement, isValidElement, toChildArray } from "preact";
import { useEffect, useId, useRef, useState } from "preact/hooks";
import { tintJson, tintShell } from "../lib/highlight.js";
import styles from "./ui.module.css";

const cx = (...names) => names.filter(Boolean).join(" ");

// `trailing` is intentionally rendered as a sibling of the <label> element, never
// inside it, so interactive controls (e.g. a Reset button) do not activate the field.
export function Field({ label, hint, trailing, children }) {
  const id = useId();
  const list = toChildArray(children);
  const control = list.length === 1 && isValidElement(list[0]) && !list[0].props?.id
    ? cloneElement(list[0], { id })
    : children;
  return (
    <div class={styles.field}>
      {(label || trailing) && (
        <span class={styles.fieldLabel}>
          {label && <label htmlFor={id}>{label}</label>}
          {trailing && <span class={styles.fieldTrailing}>{trailing}</span>}
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
  return <div class={cx(styles.row, end && styles.rowEnd)}>{children}</div>;
}

export function Button({ variant = "secondary", small, class: extra, ...props }) {
  return <button type="button" class={cx(styles.button, styles[variant], small && styles.small, extra)} {...props} />;
}

export function Segmented({ options, value, onChange, label }) {
  return (
    <div class={styles.segmented} role="radiogroup" aria-label={label}>
      {options.map((option) => (
        <button
          type="button"
          role="radio"
          key={option.value}
          aria-checked={option.value === value}
          class={cx(styles.segment, option.value === value && styles.segmentOn)}
          onClick={() => onChange(option.value)}
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
    <div class={styles.codeWrap}>
      <Button small variant="plain" class={styles.codeCopy} onClick={copy} aria-label={label}>
        {state === "copied" ? "Copied" : state === "failed" ? "Copy failed" : "Copy"}
      </Button>
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

export function Meter({ fraction, high }) {
  return (
    <div class={styles.meter}>
      <span class={cx(styles.meterFill, high && styles.meterHigh)} style={{ width: `${Math.min(1, fraction) * 100}%` }} />
    </div>
  );
}

export function Notice({ tone = "warn", children }) {
  return <p class={cx(styles.notice, styles[`notice_${tone}`])}>{children}</p>;
}

export function ErrorText({ children }) {
  return <Notice tone="error">{children}</Notice>;
}

export function Empty({ children }) {
  return <p class={styles.empty}>{children}</p>;
}

export function Meta({ children }) {
  return <span class={styles.meta}>{children}</span>;
}

export function Dot({ status }) {
  const tone = status === "ready" || status === "external" ? "ok" : status === "starting" ? "busy" : "bad";
  return <span class={cx(styles.dot, styles[`dot_${tone}`])} />;
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
      class={cx(styles.drop, over && styles.dropOver)}
      role="button"
      tabIndex={0}
      onClick={() => input.current.click()}
      onKeyDown={(event) => (event.key === "Enter" || event.key === " ") && input.current.click()}
      onDragOver={(event) => { event.preventDefault(); setOver(true); }}
      onDragLeave={() => setOver(false)}
      onDrop={(event) => { event.preventDefault(); setOver(false); pick(event.dataTransfer.files[0]); }}
    >
      <input ref={input} type="file" accept={accept} hidden onChange={(event) => pick(event.currentTarget.files[0])} />
      {children}
    </div>
  );
}
