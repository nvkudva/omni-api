import { useEffect, useRef } from "preact/hooks";

// Visibility-aware interval polling.
//
// - Skips a tick while `document.hidden`, then runs immediately on becoming visible.
// - Never overlaps ticks (a slow callback delays the next one).
// - Stops entirely when `active` is false.
export function usePolling(callback, interval, { active = true, immediate = true } = {}) {
  const ref = useRef(callback);
  ref.current = callback;

  useEffect(() => {
    if (!active) return undefined;
    let running = false;
    const run = async () => {
      if (running || document.hidden) return;
      running = true;
      try {
        await ref.current();
      } catch {
        // a failed tick must not stop the loop
      } finally {
        running = false;
      }
    };
    if (immediate) run();
    const timer = setInterval(run, interval);
    const onVisible = () => {
      if (!document.hidden) run();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [interval, active, immediate]);
}
