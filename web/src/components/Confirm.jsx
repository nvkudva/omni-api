import { useEffect, useRef } from "preact/hooks";
import { signal } from "@preact/signals";
import { Button } from "./ui.jsx";
import styles from "./Confirm.module.css";

/**
 * A modal question, rendered by `ConfirmHost` at the app root — so it wears the
 * app's theme instead of appearing as the browser's own `confirm()` box, and a
 * caller can put real content in it (the reasons a load was refused, say).
 */
export function ConfirmDialog({
  title,
  message,
  reasons = [],
  advisories = [],
  confirmLabel = "OK",
  cancelLabel = "Cancel",
  tone = "primary",
  onConfirm,
  onCancel,
}) {
  const confirmRef = useRef(null);
  useEffect(() => confirmRef.current?.focus(), []);
  return (
    <div
      class={styles.backdrop}
      onClick={onCancel}
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          event.preventDefault();
          onCancel();
        }
      }}
    >
      <div
        class={styles.dialog}
        role="alertdialog"
        aria-modal="true"
        aria-label={title}
        onClick={(event) => event.stopPropagation()}
      >
        <h2 class={styles.title}>{title}</h2>
        {message && <p class={styles.message}>{message}</p>}
        {reasons.length > 0 && (
          <ul class={styles.reasons}>
            {reasons.map((reason, index) => <li key={index}>{reason}</li>)}
          </ul>
        )}
        {advisories.length > 0 && <p class={styles.advisory}>Also worth knowing: {advisories.join("; ")}.</p>}
        <div class={styles.actions}>
          <Button onClick={onCancel}>{cancelLabel}</Button>
          <Button ref={confirmRef} variant={tone} onClick={onConfirm}>{confirmLabel}</Button>
        </div>
      </div>
    </div>
  );
}

// The one question on screen, if any: `{ options, resolve }`. Exported so a caller
// (or a test) can tell whether a dialog is open.
export const pendingConfirm = signal(null);

/**
 * Ask, and await the answer: `if (await ask({ title, ... })) …`. Callable from
 * anywhere — including plain functions outside a component — because the dialog
 * itself lives in `ConfirmHost` at the app root.
 */
export function ask(options) {
  return new Promise((resolve) => {
    pendingConfirm.value = { options, resolve };
  });
}

/** Renders whichever question is pending. Mount once, at the app root. */
export function ConfirmHost() {
  const request = pendingConfirm.value;
  if (!request) return null;
  const answer = (value) => {
    const resolve = pendingConfirm.value?.resolve;
    pendingConfirm.value = null;
    resolve?.(value);
  };
  return <ConfirmDialog {...request.options} onConfirm={() => answer(true)} onCancel={() => answer(false)} />;
}
