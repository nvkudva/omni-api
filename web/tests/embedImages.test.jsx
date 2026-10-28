/**
 * Image upload is offered only when the embed model supports images.
 *
 * Browser globals come from `tests/globals.js` (see bunfig.toml).
 */
import { afterEach, describe, expect, test } from "bun:test";
import { render } from "preact-render-to-string";

const { default: Embed } = await import("../src/tabs/Embed.jsx");
const { models } = await import("../src/api/models.js");

const textOnly = { id: "embeddinggemma-2:270m", role: "embed", capabilities: ["embedding"] };
const vision = { id: "embeddinggemma-2", role: "embed", capabilities: ["embedding", "vision"] };

afterEach(() => { models.value = { data: [], defaults: {} }; });

describe("the embed picker", () => {
  test("a text-only catalog hides image upload and says why", () => {
    models.value = { data: [textOnly], defaults: { embed: textOnly.id } };
    const html = render(<Embed />);
    expect(html).not.toContain("Add images");
    expect(html).toContain("Images need an embed model that supports them");
  });

  test("a vision-capable embed model brings image upload back", () => {
    models.value = { data: [textOnly, { ...vision, id: "embeddinggemma-2:latest" }], defaults: { embed: "embeddinggemma-2" } };
    expect(render(<Embed />)).toContain("Add images");
  });
});
