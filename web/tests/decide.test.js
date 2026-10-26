import { expect, test } from "bun:test";
import { shortenedImage } from "../src/tabs/Decide.jsx";

const PICTURE = `data:image/png;base64,${"A".repeat(400)}`;

test("a picked image shows as its head, not the whole data URL", () => {
  const body = { state: "", questions: { kind: {} } };
  const shown = shortenedImage(body, { url: PICTURE });
  expect(shown.image).toBe(`${PICTURE.slice(0, 40)}…`);
  expect(shown.questions).toBe(body.questions);
});

test("without a picked image the body is shown as it is", () => {
  const body = { state: "text", questions: {} };
  expect(shortenedImage(body, null)).toBe(body);
});
