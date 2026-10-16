import { useRef, useState } from "preact/hooks";
import styles from "./ui.module.css";

const cx = (...names) => names.filter(Boolean).join(" ");

export function Page({ title, subtitle, actions, children }) {
  return (
    <section class={styles.page}>
      <header class={styles.pageHeader}>
        <div>
          <h1 class={styles.pageTitle}>{title}</h1>
          {subtitle && <p class={styles.pageSubtitle}>{subtitle}</p>}
        </div>
        {actions && <div class={styles.row}>{actions}</div>}
      </header>
      {children}
    </section>
  );
}

export function Columns({ children }) {
  return <div class={styles.columns}>{children}</div>;
}

export function Stack({ children }) {
  return <div class={styles.stack}>{children}</div>;
}

export function Card({ title, meta, actions, flush, children }) {
  return (
    <div class={styles.card}>
      {(title || actions || meta) && (
        <div class={styles.cardHeader}>
          <div class={styles.cardTitleGroup}>
            {title && <h2 class={styles.cardTitle}>{title}</h2>}
            {meta && <span class={styles.meta}>{meta}</span>}
          </div>
          {actions && <div class={styles.row}>{actions}</div>}
        </div>
      )}
      <div class={flush ? styles.cardBodyFlush : styles.cardBody}>{children}</div>
    </div>
  );
}

export function Field({ label, hint, trailing, children }) {
  return (
    <label class={styles.field}>
      {(label || trailing) && (
        <span class={styles.fieldLabel}>
          <span>{label}</span>
          {trailing && <span class={styles.fieldTrailing}>{trailing}</span>}
        </span>
      )}
      {children}
      {hint && <span class={styles.hint}>{hint}</span>}
    </label>
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

export function CurlBlock({ code }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    await navigator.clipboard.writeText(code);
    setCopied(true);
    setTimeout(() => setCopied(false), 1200);
  };
  return (
    <details class={styles.curl}>
      <summary class={styles.curlSummary}>
        <span class={styles.chevron} aria-hidden="true" />
        <span>curl</span>
      </summary>
      <div class={styles.curlBody}>
        <Button small variant="plain" class={styles.copy} onClick={copy}>{copied ? "Copied" : "Copy"}</Button>
        <pre class={styles.code}>{code}</pre>
      </div>
    </details>
  );
}

export function Bars({ rows, format = (value) => `${(value * 100).toFixed(1)}%` }) {
  return (
    <div class={styles.bars}>
      {rows.map((row) => (
        <div class={cx(styles.bar, row.top && styles.barTop)}>
          <span class={styles.barLabel} title={row.label}>{row.label}</span>
          <span class={styles.barTrack}>
            <span class={styles.barFill} style={{ width: `${Math.max(0, Math.min(1, row.value)) * 100}%` }} />
          </span>
          <span class={styles.barValue}>{format(row.value)}</span>
        </div>
      ))}
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
  return <span class={styles.spinner} aria-label="working" />;
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
