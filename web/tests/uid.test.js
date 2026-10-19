import { describe, expect, test } from "bun:test";
import { uid } from "../src/lib/uid.js";

describe("uid", () => {
  test("falls back when crypto.randomUUID is missing (plain HTTP tailnet)", () => {
    const randomUUID = crypto.randomUUID;
    // @ts-ignore - simulate a non-secure context
    crypto.randomUUID = undefined;
    try {
      const first = uid();
      const second = uid();
      expect(typeof first).toBe("string");
      expect(first.length).toBeGreaterThan(0);
      expect(second).not.toBe(first);
    } finally {
      crypto.randomUUID = randomUUID;
    }
  });

  test("uses crypto.randomUUID when available", () => {
    expect(uid()).toMatch(/^[0-9a-f-]{36}$/);
  });
});
