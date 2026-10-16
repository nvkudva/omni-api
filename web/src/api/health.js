import { signal } from "@preact/signals";
import { getJson } from "./client.js";

export const health = signal(null);
export const healthError = signal("");

export async function pollHealth() {
  try {
    health.value = await getJson("/health");
    healthError.value = "";
  } catch (error) {
    // Keep the last good value on a transient error; never flip straight to "down".
    healthError.value = error.message;
  }
}
