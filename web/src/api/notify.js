import { signal } from "@preact/signals";
import { uid } from "../lib/uid.js";

// Toasts are the short-lived pop-ups for something the person just did; problems is the
// session's log of every error shown, so one that scrolled away can be found again. Neither is
// persisted: a refresh starts clean.
export const toasts = signal([]);
export const problems = signal([]);

const TOAST_MS = 8000;
const REPEAT_MS = 5000;
const MAX_PROBLEMS = 100;

/** Log a problem. The same message twice in a row within a few seconds is one entry. */
export function record(message, { tone = "error", source = location.pathname } = {}) {
  const latest = problems.value[0];
  if (latest?.message === message && Date.now() - latest.at < REPEAT_MS) return latest;
  const entry = { id: uid(), at: Date.now(), tone, message, source };
  problems.value = [entry, ...problems.value].slice(0, MAX_PROBLEMS);
  return entry;
}

export function dismiss(id) {
  toasts.value = toasts.value.filter((toast) => toast.id !== id);
}

/** Log a problem and pop it up: `notify("…", { title: "Could not start STT" })`. */
export function notify(message, { tone = "error", title, source } = {}) {
  const { id } = record(message, { tone, source });
  toasts.value = [...toasts.value.filter((toast) => toast.id !== id), { id, tone, title, message }];
  setTimeout(() => dismiss(id), TOAST_MS);
}

export function clearProblems() {
  problems.value = [];
}
