/**
 * The run history keeps the newest entries, frees the blob URLs it drops, and ends a run
 * for good on Stop. There is no DOM here, so the hooks it uses run on a minimal stand-in.
 */
import { afterEach, beforeEach, describe, expect, mock, test } from "bun:test";

let slots = [];
let cursor = 0;
let cleanups = [];
let faking = false;

// mock.module replaces preact/hooks for every test file in this process, so the stand-in
// must pass through to the real hooks outside this file's tests or other pages break
const real = { ...(await import("preact/hooks")) };
const fake = {
  useState(initial) {
    const index = cursor++;
    if (!(index in slots)) slots[index] = typeof initial === "function" ? initial() : initial;
    return [slots[index], (value) => { slots[index] = value; }];
  },
  useRef(initial) {
    const index = cursor++;
    if (!(index in slots)) slots[index] = { current: initial };
    return slots[index];
  },
  useEffect(effect) {
    const index = cursor++;
    if (index in slots) return;
    slots[index] = true;
    cleanups.push(effect());
  },
};

mock.module("preact/hooks", () => ({
  ...real,
  useState: (...args) => (faking ? fake.useState : real.useState)(...args),
  useRef: (...args) => (faking ? fake.useRef : real.useRef)(...args),
  useEffect: (...args) => (faking ? fake.useEffect : real.useEffect)(...args),
}));

const { useRunHistory } = await import("../src/hooks/useRunHistory.js");

const render = () => {
  cursor = 0;
  return useRunHistory();
};
const unmount = () => cleanups.forEach((cleanup) => cleanup?.());

const realFetch = globalThis.fetch;
const realRevoke = URL.revokeObjectURL;
let revoked;
let deletes;

beforeEach(() => {
  faking = true;
  slots = [];
  cleanups = [];
  revoked = [];
  deletes = [];
  URL.revokeObjectURL = (url) => revoked.push(url);
  globalThis.fetch = async (url, init) => {
    deletes.push([url, init.method]);
    return { ok: true };
  };
});

afterEach(() => {
  faking = false;
  URL.revokeObjectURL = realRevoke;
  globalThis.fetch = realFetch;
});

describe("history cap", () => {
  test("keeps the newest 20 and revokes the urls of the dropped", () => {
    const history = render();
    for (let index = 0; index < 22; index++) history.add({ id: `e${index}`, url: `blob:${index}` });
    const { entries } = render();
    expect(entries.length).toBe(20);
    expect(entries[0].id).toBe("e2");
    expect(revoked).toEqual(["blob:0", "blob:1"]);
  });

  test("update keeps an entry's url and revokes nothing", () => {
    const history = render();
    history.add({ id: "a", url: "blob:a", pending: true });
    history.update("a", { pending: false });
    expect(render().entries[0]).toEqual({ id: "a", url: "blob:a", pending: false });
    expect(revoked).toEqual([]);
  });

  test("clear revokes every url; entries without one are fine", () => {
    const history = render();
    history.add({ id: "a", url: "blob:a" });
    history.add({ id: "b" });
    history.clear();
    expect(render().entries).toEqual([]);
    expect(revoked).toEqual(["blob:a"]);
  });
});

describe("a run", () => {
  test("start hands out a signal and an id, and refuses a second run", () => {
    const history = render();
    const run = history.start();
    expect(run.signal.aborted).toBe(false);
    expect(typeof run.execId).toBe("string");
    expect(render().busy).toBe(true);
    expect(history.start()).toBeNull();
  });

  test("stop aborts the fetch and cancels the work on the gateway", () => {
    const history = render();
    const { signal, execId } = history.start();
    history.stop();
    expect(signal.aborted).toBe(true);
    expect(deletes).toEqual([[`/admin/requests/${encodeURIComponent(execId)}`, "DELETE"]]);
  });

  test("finish frees the slot for the next run", () => {
    const history = render();
    history.start();
    history.finish();
    expect(render().busy).toBe(false);
    expect(history.start()).not.toBeNull();
  });

  test("nextExec is what Stop cancels afterwards", () => {
    const history = render();
    history.start();
    const next = history.nextExec();
    history.stop();
    expect(deletes[0][0]).toContain(encodeURIComponent(next));
  });
});

describe("unmount", () => {
  test("aborts the fetch without cancelling the gateway work, and revokes every url", () => {
    const history = render();
    history.add({ id: "a", url: "blob:a" });
    const { signal } = history.start();
    unmount();
    expect(signal.aborted).toBe(true);
    expect(deletes).toEqual([]);
    expect(revoked).toEqual(["blob:a"]);
  });
});
