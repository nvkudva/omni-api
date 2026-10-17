import { signal } from "@preact/signals";
import { getJson } from "./client.js";

export const models = signal({ data: [], defaults: {} });

export async function loadModels() {
  try {
    models.value = await getJson("/v1/models");
  } catch {
    // keep the last good catalog: one transient failure must not blank every tab
  }
}
