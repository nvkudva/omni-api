// Turning files into searchable passages, and ranking passages against a query.
// The embedding model is text-only, so images and audio enter as derived text
// (a vision caption, an STT transcript) and are chunked like any other text.

// The server silently truncates inputs at 2000 characters; stay well under it.
export const CHUNK_CHARS = 1500;
export const CHUNK_OVERLAP = 150;
export const MAX_TEXT_BYTES = 2 * 1024 * 1024;

const TEXT_EXTENSIONS = new Set([
  "txt", "md", "markdown", "rst", "csv", "tsv", "json", "jsonl", "yaml", "yml", "toml", "ini", "xml", "html", "htm", "css",
  "js", "mjs", "cjs", "jsx", "ts", "tsx", "py", "rb", "go", "rs", "java", "kt", "c", "h", "cpp", "hpp", "cs", "swift",
  "sh", "zsh", "sql", "log", "tex",
]);

const SKIPPED_PATH = /(^|\/)(node_modules|\.git|__pycache__|dist|build)\//;

/** "text", "image", "audio", or null when the file cannot be indexed. */
export function kindOf(name, mimeType) {
  if (mimeType?.startsWith("image/")) return "image";
  if (mimeType?.startsWith("audio/")) return "audio";
  if (mimeType?.startsWith("text/")) return "text";
  const extension = name.includes(".") ? name.split(".").pop().toLowerCase() : "";
  return TEXT_EXTENSIONS.has(extension) ? "text" : null;
}

export function isSkippedPath(path) {
  return SKIPPED_PATH.test(path) || path.split("/").some((part) => part.startsWith(".") && part.length > 1);
}

function breakPoint(text, start, limit) {
  if (limit >= text.length) return text.length;
  const window = text.slice(start, limit);
  const paragraph = window.lastIndexOf("\n\n");
  if (paragraph > window.length / 2) return start + paragraph + 2;
  const line = window.lastIndexOf("\n");
  if (line > window.length / 2) return start + line + 1;
  const space = window.lastIndexOf(" ");
  if (space > window.length / 2) return start + space + 1;
  return limit;
}

/** Split text into passages of at most `size` characters, overlapping slightly. */
export function chunkText(text, size = CHUNK_CHARS, overlap = CHUNK_OVERLAP) {
  const clean = text.replace(/\r\n/g, "\n").trim();
  const chunks = [];
  let start = 0;
  while (start < clean.length) {
    const end = breakPoint(clean, start, start + size);
    const piece = clean.slice(start, end).trim();
    if (piece) chunks.push(piece);
    if (end >= clean.length) break;
    start = Math.max(end - overlap, start + 1);
  }
  return chunks;
}

export function dot(left, right) {
  let sum = 0;
  for (let index = 0; index < left.length; index++) sum += left[index] * right[index];
  return sum;
}

export function isVector(value) {
  return value?.length > 0 && Array.from(value).every(Number.isFinite);
}

/** The `limit` chunks most similar to `queryVector`. Vectors are normalised, so dot is cosine. */
export function search(queryVector, chunks, limit = 5) {
  return chunks
    .filter((chunk) => chunk.embedding.length === queryVector.length)
    .map((chunk) => ({ chunk, score: dot(queryVector, chunk.embedding) }))
    .sort((left, right) => right.score - left.score)
    .slice(0, limit);
}

export function snippet(text, length = 90) {
  const flat = text.replace(/\s+/g, " ").trim();
  return flat.length > length ? `${flat.slice(0, length)}…` : flat;
}
