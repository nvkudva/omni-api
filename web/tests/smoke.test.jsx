/**
 * Renders every page to HTML without a browser. This catches the failure the
 * production build cannot: a page that compiles but throws the moment it
 * renders — a missing export, a bad prop, a null someone forgot to guard.
 *
 * Browser globals come from `tests/globals.js` (see bunfig.toml). The pages are
 * loaded dynamically so those globals exist before the app modules run.
 */
import { beforeEach, describe, expect, test } from "bun:test";
import { render } from "preact-render-to-string";

const { default: App } = await import("../src/App.jsx");
const { runtime, ROLE_META, ROLE_ORDER } = await import("../src/api/roles.js");
const { models } = await import("../src/api/models.js");
const { health } = await import("../src/api/health.js");
const { gb } = await import("../src/lib/format.js");
const pages = {
  Chat: (await import("../src/tabs/Chat.jsx")).default,
  Decide: (await import("../src/tabs/Decide.jsx")).default,
  Embed: (await import("../src/tabs/Embed.jsx")).default,
  Image: (await import("../src/tabs/Image.jsx")).default,
  Video: (await import("../src/tabs/Video.jsx")).default,
  Stt: (await import("../src/tabs/Stt.jsx")).default,
  Tts: (await import("../src/tabs/Tts.jsx")).default,
  Models: (await import("../src/tabs/Models.jsx")).default,
  Health: (await import("../src/tabs/Health.jsx")).default,
};

const ENDPOINTS = {
  Chat: "POST /v1/chat/completions",
  Decide: "POST /v1/systemone",
  Embed: "POST /v1/embeddings",
  Image: "POST /v1/images/generations",
  Video: "POST /v1/videos",
  Stt: "POST /v1/audio/transcriptions",
  Tts: "POST /v1/audio/speech",
};

// nothing has answered yet: what the app looks like on a cold load, or with the
// gateway unreachable.
function reset() {
  runtime.value = { ready: false, error: "", roles: {}, warnings: [], memory: null };
  models.value = { data: [], defaults: {} };
  health.value = null;
}

// The empty runtime is the easy case. This is what a live gateway produces for
// every role at once: a model name, memory in use, and a ready worker.
const MODEL = "org/model-4bit";
const BYTES = 5_368_709_120;

function seed() {
  runtime.value = {
    ready: true,
    error: "",
    warnings: ["9.4 GB swapped out: models will run slowly"],
    memory: null,
    roles: Object.fromEntries(ROLE_ORDER.map((role) => [role, {
      ...ROLE_META[role], role, model: MODEL, loaded: true, runningModel: MODEL,
      memoryBytes: BYTES, status: "ready", cpuPercent: 12.5, held: false,
    }])),
  };
  models.value = {
    data: ROLE_ORDER.map((role) => ({ id: MODEL, object: "model", owned_by: "omni", role, capabilities: [] })),
    defaults: Object.fromEntries(ROLE_ORDER.map((role) => [role, MODEL])),
  };
  health.value = {
    ok: true,
    workers: Object.fromEntries(ROLE_ORDER.map((role) => [role, {
      status: "ready", pid: 4242, restarts: 0, rss_bytes: BYTES, cpu_percent: 12.5,
      held: false, model: MODEL, loaded: true,
    }])),
  };
}

describe("pages render before the first poll", () => {
  beforeEach(reset);

  for (const [name, Page] of Object.entries(pages)) {
    test(name, () => {
      const html = render(<Page active />);
      expect(html.length).toBeGreaterThan(200);
      // nothing may leak an unformatted value into the markup
      expect(html).not.toContain(">undefined<");
      expect(html).not.toContain("NaN");
      expect(html).not.toContain("[object Object]");
    });
  }

  test("App renders the sidebar and a role page", () => {
    const html = render(<App />);
    for (const word of ["Chat", "Speech to text", "Models", "Health"]) expect(html).toContain(word);
    // every role card carries its own endpoint, and a switch that is not yet usable
    expect(html).toContain("POST /v1/chat/completions");
    expect(html).toContain("POST /v1/audio/transcriptions");
    expect(html).toContain("checking…");
    expect(html).toContain("waiting for the gateway");
  });

  test("each page shows its own endpoint", () => {
    for (const [name, endpoint] of Object.entries(ENDPOINTS)) {
      const Page = pages[name];
      expect(render(<Page active />)).toContain(endpoint);
    }
  });
});

describe("pages render against a live-looking runtime", () => {
  beforeEach(seed);

  test("the sidebar shows each role's model, memory and loaded state", () => {
    const html = render(<App />);
    expect(html).toContain(MODEL);
    expect(html).toContain(gb(BYTES));
    expect(html).toContain("running");
    expect(html).not.toContain("waiting for the gateway");
  });

  test("every page renders with a loaded model and no leaks", () => {
    for (const [name, Page] of Object.entries(pages)) {
      const html = render(<Page active />);
      // Models fetches its catalog in an effect, which never runs here
      if (name !== "Models") expect(html).toContain(MODEL);
      expect(html).not.toContain("undefined");
      expect(html).not.toContain("NaN");
      expect(html).not.toContain("[object Object]");
    }
  });
});
