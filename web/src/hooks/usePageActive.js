import { createContext } from "preact";
import { useContext } from "preact/hooks";

// Whether the tab page this component sits in is the visible one; pages stay mounted when hidden.
export const PageActive = createContext(true);

export function usePageActive() {
  return useContext(PageActive);
}
