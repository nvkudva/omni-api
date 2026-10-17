import { useEffect, useRef } from "preact/hooks";

import { signal } from "@preact/signals";
import { Button } from "./ui.jsx";
import { runtime } from "../api/roles.js";
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
  const dialogRef = useRef(null);
  useEffect(() => {
    const opener = document.activeElement;
    confirmRef.current?.focus();
    const trap = (event) => {
      if (event.key === "Escape") { event.preventDefault(); onCancel(); return; }
      if (event.key !== "Tab" || !dialogRef.current) return;
      const focusable = [...dialogRef.current.querySelectorAll("button, [href], input, select, textarea, [tabIndex]")];
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", trap, true);
    return () => { document.removeEventListener("keydown", trap, true); opener?.focus?.(); };
  }, []);
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
        ref={dialogRef}
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
  pendingConfirm.value?.resolve?.(false); // never leave an overlapped caller hanging
  return new Promise((resolve) => {
    pendingConfirm.value = { options, resolve };
  });
}

/**
 * Run a gateway action that may be refused for something about the model (a 409 `overload` with
 * `blockers`): show the blockers, and retry with `{ force: true }` if the person says go. Resolves to
 * the action's result, or null when they decline; any other failure is thrown.
 */
export async function withForce(action, { title, message }) {
  try {
    return await action({});
  } catch (error) {
    if (error.code !== "overload") throw error;
    const go = await ask({
      title,
      message,
      reasons: error.blockers,
      advisories: runtime.value.warnings,
      confirmLabel: "Load anyway",
      cancelLabel: "Not now",
    });
    return go ? action({ force: true }) : null;
  }
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
