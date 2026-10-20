// Unique ids for turns, messages and editor rows. `crypto.randomUUID` only
// exists in secure contexts (https, localhost); the playground is also served
// over plain HTTP to tailnet IPs, so fall back to a counter + random suffix.
let counter = 0;

export function uid() {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  counter += 1;
  const random = Math.floor(Math.random() * 0xffffffff).toString(16).padStart(8, "0");
  return `id-${Date.now().toString(36)}-${counter.toString(36)}-${random}`;
}
