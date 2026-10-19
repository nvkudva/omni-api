import { signal } from "@preact/signals";
import { getJson } from "./client.js";
import { uid } from "../lib/uid.js";

export const EXEC_HEADER = "x-omni-exec";

// Retained inference executions the gateway reports: what is running now and what finished
// lately, whatever tab or browser session started them. A run started here keeps its id in
// the request header, so closing the tab, navigating away or closing the browser never loses
// it: the gateway completes the work and the result stays retrievable under /admin/requests.
export const execs = signal({ running: [], recent: [] });

export function newExecId() {
  return uid();
}

export function execHeaders(execId) {
  return execId ? { [EXEC_HEADER]: execId } : {};
}

/** Refresh the global executions list. Keeps the last good set on transient failure. */
export async function pollExecs() {
  try {
    execs.value = await getJson("/admin/requests");
  } catch {
    // one failed poll must not blank the panel
  }
  return execs.value;
}

/** Fetch one retained result as a blob with its stored content type. */
export async function execResult(execId) {
  const response = await fetch(`/admin/requests/${encodeURIComponent(execId)}/result`);
  if (!response.ok) throw new Error(`request ${execId}: ${response.status}`);
  return { blob: await response.blob(), contentType: response.headers.get("content-type") ?? "" };
}

/** Stop a run for good: abort this tab's fetch and cancel the work on the gateway. Closing the
 *  tab only drops the fetch and the gateway finishes the work; Stop is the one way to end it. */
export function cancelExec(controller, execId) {
  controller?.abort();
  if (!execId) return;
  fetch(`/admin/requests/${encodeURIComponent(execId)}`, { method: "DELETE" }).catch(() => {});
}
