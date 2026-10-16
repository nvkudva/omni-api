import { useEffect, useMemo, useState } from "preact/hooks";

/**
 * Drives a request panel's UI/JSON switch.
 *
 * `body` is the object the UI fields build; while the JSON pane is open it
 * becomes the source of truth, so the body that is actually sent is whatever the
 * pane parses. Sliding into JSON re-serialises from the fields.
 */
export function useJsonRequest(view, body) {
  const serialized = JSON.stringify(body, null, 2);
  const [text, setText] = useState(serialized);

  useEffect(() => {
    if (view === "json") setText(serialized);
  }, [view, serialized]);

  const resolved = useMemo(() => {
    if (view !== "json") return { body, error: "" };
    try {
      return { body: JSON.parse(text), error: "" };
    } catch (error) {
      return { body: null, error: error.message };
    }
  }, [view, text, body]);

  return { text, setText, body: resolved.body, error: resolved.error, serialized };
}
