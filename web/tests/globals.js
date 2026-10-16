/**
 * Browser globals for the render tests, preloaded by `bunfig.toml`.
 *
 * App modules read a few of these at import time (`api/client.js` reads
 * `location.origin`; `store.js` reads localStorage), and a couple more inside
 * `useEffect`, which `preact-render-to-string` never runs — so they only need to
 * exist, not to work.
 */
const written = new Map();

globalThis.location = { origin: "http://127.0.0.1:10000", pathname: "/", hash: "", search: "" };
globalThis.localStorage = {
  getItem: (key) => (written.has(key) ? written.get(key) : null),
  setItem: (key, value) => written.set(key, String(value)),
  removeItem: (key) => written.delete(key),
};
globalThis.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
globalThis.document = {
  hidden: false,
  documentElement: { dataset: {} },
  addEventListener() {},
  removeEventListener() {},
};
globalThis.getComputedStyle = () => ({ getPropertyValue: () => "" });
globalThis.URL.createObjectURL ??= () => "blob:test";
globalThis.URL.revokeObjectURL ??= () => {};
