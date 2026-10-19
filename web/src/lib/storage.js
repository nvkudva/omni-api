// How much browser storage the embedded data takes. The index is estimated from the
// records themselves; the browser reports its own, origin-wide figure separately.

const BYTES_PER_FLOAT = 4;

/**
 * Approximate IndexedDB bytes per file name: its vectors, passage text, ids and
 * thumbnail. Structured-clone overhead is not counted, so these are close, not exact.
 */
export function footprints(files, chunks) {
  const bytes = new Map(files.map((file) => [file.name, file.name.length + (file.thumb?.length ?? 0)]));
  for (const chunk of chunks) {
    if (!bytes.has(chunk.file)) continue;
    const own = chunk.embedding.length * BYTES_PER_FLOAT + chunk.text.length + chunk.id.length;
    bytes.set(chunk.file, bytes.get(chunk.file) + own);
  }
  return bytes;
}

export function total(footprint) {
  let sum = 0;
  for (const bytes of footprint.values()) sum += bytes;
  return sum;
}

/** `{ usage, quota }` for this whole origin, or null where the browser cannot say. */
export async function browserUsage() {
  try {
    const { usage, quota } = await navigator.storage.estimate();
    return Number.isFinite(usage) ? { usage, quota } : null;
  } catch {
    return null;
  }
}
