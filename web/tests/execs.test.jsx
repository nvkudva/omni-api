/**
 * Retained inference: every playground request carries an execution id the gateway keeps,
 * and the global list feeds the executions panel on every tab.
 */
import { beforeEach, describe, expect, test } from "bun:test";

const { chatStream } = await import("../src/api/endpoints.js");
const { execs, execHeaders, newExecId, pollExecs } = await import("../src/api/execs.js");

const realFetch = globalThis.fetch;

beforeEach(() => {
  execs.value = { running: [], recent: [] };
  globalThis.fetch = realFetch;
});

describe("execution ids", () => {
  test("a chat request carries its id in the exec header", async () => {
    let seen;
    globalThis.fetch = async (_url, init) => {
      seen = init.headers;
      return { ok: true, status: 200 };
    };
    await chatStream({ model: "m", messages: [], stream: false }, undefined, "turn-1");
    expect(seen["x-omni-exec"]).toBe("turn-1");
  });

  test("no id means no header", async () => {
    let seen;
    globalThis.fetch = async (_url, init) => {
      seen = init.headers;
      return { ok: true, status: 200 };
    };
    await chatStream({ model: "m", messages: [], stream: false }, undefined);
    expect(seen["x-omni-exec"]).toBeUndefined();
  });

  test("generated ids are gateway-valid", () => {
    expect(newExecId()).toMatch(/^[A-Za-z0-9\-.]+$/);
  });

  test("header helper is empty without an id", () => {
    expect(execHeaders(undefined)).toEqual({});
    expect(execHeaders("a")).toEqual({ "x-omni-exec": "a" });
  });
});

describe("the executions feed", () => {
  test("poll fills running and recent for the panel", async () => {
    globalThis.fetch = async () => ({
      ok: true,
      json: async () => ({
        running: [{ id: "a", role: "chat", path: "/v1/chat/completions", bytes: 12 }],
        recent: [{ id: "b", role: "tts", path: "/v1/audio/speech", size: 99, finished_at: 1 }],
      }),
    });
    await pollExecs();
    expect(execs.value.running).toHaveLength(1);
    expect(execs.value.recent).toHaveLength(1);
  });

  test("a failed poll keeps the last good list", async () => {
    execs.value = { running: [{ id: "a" }], recent: [] };
    globalThis.fetch = async () => { throw new Error("down"); };
    await pollExecs();
    expect(execs.value.running).toHaveLength(1);
  });
});
