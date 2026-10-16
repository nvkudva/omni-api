import { useEffect, useState } from "preact/hooks";
import * as store from "../store.js";

export function useTheme() {
  const [theme, setTheme] = useState(() => store.get("omni.theme", "system"));
  useEffect(() => {
    const media = matchMedia("(prefers-color-scheme: dark)");
    const apply = () => {
      const resolved = theme === "system" ? (media.matches ? "dark" : "light") : theme;
      document.documentElement.dataset.theme = resolved;
    };
    apply();
    store.set("omni.theme", theme);
    media.addEventListener("change", apply);
    return () => media.removeEventListener("change", apply);
  }, [theme]);
  return [theme, setTheme];
}
