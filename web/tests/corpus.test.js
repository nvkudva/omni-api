import { describe, expect, test } from "bun:test";
import * as corpus from "../src/lib/corpus.js";
import { size } from "../src/lib/format.js";
import * as storage from "../src/lib/storage.js";

describe("chunking", () => {
  test("short text is one chunk", () => {
    expect(corpus.chunkText("hello world")).toEqual(["hello world"]);
  });

  test("blank text yields nothing", () => {
    expect(corpus.chunkText("  \n\n ")).toEqual([]);
  });

  test("long text splits under the size, overlaps, and loses no words", () => {
    const words = Array.from({ length: 1200 }, (_, index) => `word${index}`);
    const chunks = corpus.chunkText(words.join(" "), 500, 50);
    expect(chunks.length).toBeGreaterThan(5);
    for (const chunk of chunks) expect(chunk.length).toBeLessThanOrEqual(500);
    const seen = new Set(chunks.flatMap((chunk) => chunk.split(" ")));
    for (const word of words) expect(seen.has(word)).toBe(true);
    const tailOfFirst = chunks[0].split(" ").slice(-2);
    expect(chunks[1].split(" ")).toEqual(expect.arrayContaining(tailOfFirst));
  });

  test("prefers paragraph boundaries", () => {
    const text = `${"a".repeat(300)}\n\n${"b".repeat(300)}`;
    const chunks = corpus.chunkText(text, 400, 0);
    expect(chunks).toEqual(["a".repeat(300), "b".repeat(300)]);
  });

  test("text with no break points still terminates", () => {
    const chunks = corpus.chunkText("x".repeat(1000), 300, 30);
    expect(chunks.every((chunk) => chunk.length <= 300)).toBe(true);
    expect(chunks.length).toBeGreaterThan(3);
  });
});

describe("file kinds", () => {
  test("by mime type, then extension", () => {
    expect(corpus.kindOf("a.png", "image/png")).toBe("image");
    expect(corpus.kindOf("a.wav", "audio/wav")).toBe("audio");
    expect(corpus.kindOf("a.md", "")).toBe("text");
    expect(corpus.kindOf("photo.HEIC", "")).toBe("image");
    expect(corpus.kindOf("scan.jpeg", "")).toBe("image");
    expect(corpus.kindOf("a.pdf", "application/pdf")).toBeNull();
    expect(corpus.kindOf("Makefile", "")).toBeNull();
  });

  test("hidden and dependency directories are skipped", () => {
    expect(corpus.isSkippedPath("proj/node_modules/x/a.js")).toBe(true);
    expect(corpus.isSkippedPath("proj/.git/config")).toBe(true);
    expect(corpus.isSkippedPath("proj/.DS_Store")).toBe(true);
    expect(corpus.isSkippedPath("proj/src/a.js")).toBe(false);
  });
});

describe("search", () => {
  const chunks = [
    { id: "a#0", embedding: [1, 0] },
    { id: "b#0", embedding: [0.8, 0.6] },
    { id: "c#0", embedding: [0, 1] },
    { id: "d#0", embedding: [1, 0, 0] },
  ];

  test("ranks by dot product, best first, and respects the limit", () => {
    const found = corpus.search([1, 0], chunks, 2);
    expect(found.map((hit) => hit.chunk.id)).toEqual(["a#0", "b#0"]);
    expect(found[0].score).toBe(1);
  });

  test("ignores chunks of another dimension", () => {
    const found = corpus.search([1, 0], chunks, 10);
    expect(found.map((hit) => hit.chunk.id)).not.toContain("d#0");
  });
});

describe("storage footprint", () => {
  const files = [{ name: "a.md", thumb: "x".repeat(10) }, { name: "b.md" }];
  const chunks = [
    { id: "a.md#0", file: "a.md", text: "hello", embedding: new Float32Array(8) },
    { id: "a.md#1", file: "a.md", text: "world", embedding: new Float32Array(8) },
    { id: "gone#0", file: "gone", text: "orphan", embedding: new Float32Array(8) },
  ];

  test("counts vectors, text, ids and thumbnails per file", () => {
    const footprint = storage.footprints(files, chunks);
    expect(footprint.get("a.md")).toBe(4 + 10 + 2 * (8 * 4 + 5 + 6));
    expect(footprint.get("b.md")).toBe(4);
    expect(footprint.has("gone")).toBe(false);
    expect(storage.total(footprint)).toBe(footprint.get("a.md") + 4);
  });

  test("browser usage is null when the API is missing", async () => {
    expect(await storage.browserUsage()).toBeNull();
  });
});

describe("byte sizes", () => {
  test("scale units", () => {
    expect(size(512)).toBe("512 B");
    expect(size(2048)).toBe("2 KB");
    expect(size(5 * 1024 ** 2)).toBe("5.0 MB");
    expect(size(3 * 1024 ** 3)).toBe("3.0 GB");
  });
});
