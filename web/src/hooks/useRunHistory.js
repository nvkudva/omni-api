import { useEffect, useRef, useState } from "preact/hooks";
import { cancelExec, newExecId } from "../api/execs.js";

const MAX_ENTRIES = 20;

function revokeUrl(entry) {
  if (entry.url) URL.revokeObjectURL(entry.url);
}

/** One run at a time plus its capped history. Entries own their `url` (a blob URL): it is
 *  revoked when the entry is dropped, cleared or the tab unmounts. */
export function useRunHistory() {
  const [entries, setEntries] = useState([]);
  const [busy, setBusy] = useState(false);
  const entriesRef = useRef([]);
  const abort = useRef(null);
  const execId = useRef(null);

  useEffect(() => () => {
    abort.current?.abort();
    entriesRef.current.forEach(revokeUrl);
  }, []);

  const commit = (next) => {
    const kept = next.slice(-MAX_ENTRIES);
    const keptIds = new Set(kept.map((entry) => entry.id));
    entriesRef.current.filter((entry) => !keptIds.has(entry.id)).forEach(revokeUrl);
    entriesRef.current = kept;
    setEntries(kept);
  };

  /** Begin a run, or return null while one is still going. */
  const start = () => {
    if (abort.current) return null;
    const controller = new AbortController();
    abort.current = controller;
    execId.current = newExecId();
    setBusy(true);
    return { signal: controller.signal, execId: execId.current };
  };

  return {
    entries,
    busy,
    start,
    nextExec: () => (execId.current = newExecId()),
    stop: () => cancelExec(abort.current, execId.current),
    finish: () => {
      abort.current = null;
      setBusy(false);
    },
    find: (id) => entriesRef.current.find((entry) => entry.id === id),
    add: (entry) => commit([...entriesRef.current, entry]),
    update: (id, changes) => commit(entriesRef.current.map((entry) => (entry.id === id ? { ...entry, ...changes } : entry))),
    clear: () => commit([]),
  };
}
