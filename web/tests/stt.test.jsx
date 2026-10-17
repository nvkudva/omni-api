/**
 * The speech-to-text transcript must always show something playable and
 * readable: its own audio, the words, or an explicit "nothing heard".
 *
 * Browser globals come from `tests/globals.js` (see bunfig.toml).
 */
import { describe, expect, test } from "bun:test";
import { render } from "preact-render-to-string";

const { Transcript, transcriptText } = await import("../src/tabs/Stt.jsx");

const VERBOSE = {
  format: "verbose_json",
  url: "blob:test-clip",
  request: { file: "speech.wav", response_format: "verbose_json" },
  result: {
    task: "transcribe",
    duration: 3.1,
    text: "Hello from omni.",
    segments: [{ id: 0, start: 0, end: 3.1, text: "Hello from omni." }],
  },
};

describe("the transcript panel", () => {
  test("a finished turn shows its player and its words", () => {
    const html = render(<Transcript turn={VERBOSE} />);
    expect(html).toContain("<audio");
    expect(html).toContain("blob:test-clip");
    expect(html).toContain("Hello from omni.");
    expect(html).toContain("0.00–3.10");
  });

  test("silence says so instead of rendering blank", () => {
    const html = render(<Transcript turn={{ ...VERBOSE, result: { ...VERBOSE.result, text: "", segments: [] } }} />);
    expect(html).toContain("<audio");
    expect(html).toContain("No speech detected in this clip.");
  });

  test("plain-text answers render as-is", () => {
    const html = render(<Transcript turn={{ format: "text", url: "blob:test-clip", result: "just words" }} />);
    expect(html).toContain("just words");
  });

  test("transcriptText reads every format, never undefined", () => {
    expect(transcriptText(VERBOSE)).toBe("Hello from omni.");
    expect(transcriptText({ format: "text", result: "just words" })).toBe("just words");
    expect(transcriptText({ format: "verbose_json", result: { text: "" } })).toBe("");
    expect(transcriptText({ format: "json", result: null })).toBe("");
  });
});
