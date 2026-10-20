import { useEffect, useRef } from "preact/hooks";

const FOCUSABLE = 'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

// Open modals, oldest first. Only the last one answers Escape and Tab.
const stack = [];

/**
 * Modal behavior for a dialog element: Escape closes only the top-most modal, Tab cycles inside
 * it, focus moves in on open (to `initialFocusRef` or the first control) and back on close.
 */
export function useModal(dialogRef, onClose, initialFocusRef) {
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    const token = {};
    stack.push(token);
    const opener = document.activeElement;
    const dialog = dialogRef.current;
    const first = initialFocusRef?.current ?? dialog?.querySelector(FOCUSABLE) ?? dialog;
    if (first === dialog && dialog && dialog.tabIndex < 0) dialog.tabIndex = -1;
    first?.focus?.();
    const onKey = (event) => {
      if (stack[stack.length - 1] !== token) return;
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        closeRef.current?.();
        return;
      }
      if (event.key !== "Tab" || !dialogRef.current) return;
      const items = [...dialogRef.current.querySelectorAll(FOCUSABLE)];
      if (!items.length) { event.preventDefault(); return; }
      const head = items[0];
      const tail = items[items.length - 1];
      if (!dialogRef.current.contains(document.activeElement)) { event.preventDefault(); head.focus(); }
      else if (event.shiftKey && document.activeElement === head) { event.preventDefault(); tail.focus(); }
      else if (!event.shiftKey && document.activeElement === tail) { event.preventDefault(); head.focus(); }
    };
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("keydown", onKey, true);
      stack.splice(stack.indexOf(token), 1);
      opener?.focus?.();
    };
  }, []);
}
