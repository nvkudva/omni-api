import { beforeEach, describe, expect, test } from "bun:test";
import { clearProblems, dismiss, notify, problems, record, toasts } from "../src/api/notify.js";

describe("notify", () => {
  beforeEach(() => {
    clearProblems();
    toasts.value = [];
  });

  test("a repeated message within seconds is one problem", () => {
    record("boom");
    record("boom");
    record("other");
    expect(problems.value.map((entry) => entry.message)).toEqual(["other", "boom"]);
  });

  test("notify pops a toast and logs it; dismiss removes only the toast", () => {
    notify("stt cannot run", { title: "Could not start STT" });
    expect(toasts.value).toHaveLength(1);
    expect(problems.value).toHaveLength(1);
    dismiss(toasts.value[0].id);
    expect(toasts.value).toHaveLength(0);
    expect(problems.value).toHaveLength(1);
  });

  test("the log keeps the newest hundred", () => {
    for (let index = 0; index < 120; index++) record(`failure ${index}`);
    expect(problems.value).toHaveLength(100);
    expect(problems.value[0].message).toBe("failure 119");
  });
});
