/**
 * Renders every page to HTML without a browser. This catches the failure the
 * production build cannot: a page that compiles but throws the moment it
 * renders — a missing export, a bad prop, a null someone forgot to guard.
 *
 * Browser globals come from `tests/globals.js` (see bunfig.toml). The pages are
 * loaded dynamically so those globals exist before the app modules run.
 */
import { afterEach, beforeEach, describe, expect, test } from "bun:test";
import { render } from "preact-render-to-string";

const { default: App } = await import("../src/App.jsx");
const { runtime, ROLE_META, ROLE_ORDER } = await import("../src/api/roles.js");
const { models } = await import("../src/api/models.js");
const { health } = await import("../src/api/health.js");
const { gb } = await import("../src/lib/format.js");
const { ask, ConfirmHost, ConfirmDialog, pendingConfirm } = await import("../src/components/Confirm.jsx");
const pages = {
  Chat: (await import("../src/tabs/Chat.jsx")).default,
  Decide: (await import("../src/tabs/Decide.jsx")).default,
  Embed: (await import("../src/tabs/Embed.jsx")).default,
  Image: (await import("../src/tabs/Image.jsx")).default,
  Video: (await import("../src/tabs/Video.jsx")).default,
  Stt: (await import("../src/tabs/Stt.jsx")).default,
  Tts: (await import("../src/tabs/Tts.jsx")).default,
  Models: (await import("../src/tabs/Models.jsx")).default,
  Settings: (await import("../src/tabs/Settings.jsx")).default,
};

const ENDPOINTS = {
  Chat: "/v1/chat/completions",
  Decide: "/v1/systemone",
  Embed: "/v1/embeddings",
  Image: "/v1/images/generations",
  Video: "/v1/videos",
  Stt: "/v1/audio/transcriptions",
  Tts: "/v1/audio/speech",
};

// nothing has answered yet: what the app looks like on a cold load, or with the
// gateway unreachable.
function reset() {
  runtime.value = { ready: false, error: "", roles: {}, warnings: [], memory: null, pins: [] };
  models.value = { data: [], defaults: {} };
  health.value = null;
}

// The empty runtime is the easy case. This is what a live gateway produces for
// every role at once: a model name, memory in use, and a ready worker.
const MODEL = "org/model-4bit";
const BYTES = 5_368_709_120;

function seed({ pinned = [] } = {}) {
  runtime.value = {
    ready: true,
    error: "",
    warnings: ["9.4 GB swapped out: models will run slowly"],
    memory: null,
    pins: pinned,
    roles: Object.fromEntries(ROLE_ORDER.map((role) => [role, {
      ...ROLE_META[role], role, model: MODEL, loaded: true, pinned: pinned.includes(role),
      runningModel: MODEL, memoryBytes: BYTES, modelBytes: BYTES, status: "ready", cpuPercent: 12.5, held: false,
    }])),
  };
  models.value = {
    data: ROLE_ORDER.map((role) => ({ id: MODEL, object: "model", owned_by: "omni", role, capabilities: [] })),
    defaults: Object.fromEntries(ROLE_ORDER.map((role) => [role, MODEL])),
  };
  health.value = {
    ok: true,
    pins: pinned,
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

  test("App renders the header, the role cards and a role page", () => {
    const html = render(<App />);
    for (const word of ["Chat", "Speech to text", "Settings"]) expect(html).toContain(word);
    // every role card carries its own endpoint, and a switch that is not yet usable
    expect(html).toContain("/v1/chat/completions");
    expect(html).toContain("/v1/audio/transcriptions");
    expect(html).toContain("checking…");
    expect(html).toContain("waiting for the gateway");
  });

  test("Settings lists its sections beside the server controls", () => {
    const html = render(<pages.Settings />);
    for (const word of ["Server", "Models", "Back to Chat", "Memory"]) expect(html).toContain(word);
    for (const word of ["Runtime", "Appearance", ">Logs<", ">Health<"]) expect(html).not.toContain(word);
  });

  test("a role page greys out its request until the model can serve", () => {
    for (const [name, Page] of Object.entries(pages)) {
      if (name === "Models" || name === "Settings") continue;
      const html = render(<Page active />);
      // nothing is known yet: the header says so, and the request sits there disabled
      expect(html).toContain(ENDPOINTS[name]);
      expect(html).toContain("is getting ready");
      expect(html).toContain("inert");
    }
  });

  test("each role page header has a model settings button", () => {
    for (const [name, Page] of Object.entries(pages)) {
      if (name === "Models" || name === "Settings") continue;
      const html = render(<Page active />);
      expect(html).toContain("model settings");
    }
  });
});

describe("pages render against a live-looking runtime", () => {
  beforeEach(seed);

  test("the Decide page offers an image only when the decide model can read one", () => {
    expect(render(<pages.Decide active />)).toContain("Drop an invoice");
    runtime.value = {
      ...runtime.value,
      roles: { ...runtime.value.roles, decide: { ...runtime.value.roles.decide, readsImages: false } },
    };
    expect(render(<pages.Decide active />)).not.toContain("Drop an invoice");
  });

  test("voices that are not Kokoro codes are labelled by name", async () => {
    const { voiceLabel } = await import("../src/tabs/Tts.jsx");
    expect(voiceLabel("Bella")).toBe("Bella");
    expect(voiceLabel("M1")).toBe("M1");
    expect(voiceLabel("af_heart")).toBe("heart · American female");
  });

  test("the role cards show each role's model, memory and loaded state", () => {
    const html = render(<App />);
    expect(html).toContain(MODEL);
    // cards show the short name, with the full id behind the tooltip
    expect(html).toContain('title="org/model-4bit"');
    expect(html).toContain(">model-4bit<");
    expect(html).toContain(gb(BYTES));
    expect(html).toContain("loaded");
    expect(html).not.toContain("waiting for the gateway");
  });

  test("every page renders against a live runtime and no leaks", () => {
    for (const [name, Page] of Object.entries(pages)) {
      const html = render(<Page active />);
      // The role card above the page carries the live model; the page itself
      // prints the endpoint it talks to, and nothing may leak into the markup.
      if (name !== "Models" && name !== "Settings") expect(html).toContain(ENDPOINTS[name]);
      expect(html).not.toContain("undefined");
      expect(html).not.toContain("NaN");
      expect(html).not.toContain("[object Object]");
    }
  });
});

describe("keeping a model warm", () => {
  beforeEach(reset);

  test("a pinned role reads as warm, with its switch on", () => {
    seed({ pinned: ["chat"] });
    const html = render(<App />);
    expect(html).toContain(">loaded · kept<");
    expect(html).toContain(">Unload<");
    expect(html).toContain("stop keeping warm Chat");
  });

  test("a pinned role whose model is still loading says so", () => {
    seed({ pinned: ["chat"] });
    runtime.value = {
      ...runtime.value,
      roles: { ...runtime.value.roles, chat: { ...runtime.value.roles.chat, loaded: false } },
    };
    const html = render(<App />);
    // pinned but not resident: on its way, not idle. Card statuses render in
    // spans; every page stays mounted now, and hidden pages may say "idle" in
    // their own readouts, so scope the check to the cards.
    expect(html).toContain("loading model…");
    expect(html).not.toContain(">idle</span>");
  });

  test("an unpinned role is not claimed as warm", () => {
    seed();
    const html = render(<App />);
    expect(html).not.toContain(">loaded · kept<");
    expect(html).toContain("keep warm Chat");
  });
});

describe("confirmations render in the app, not the browser", () => {
  afterEach(() => {
    pendingConfirm.value = null;
  });

  test("the dialog shows each reason as a list item", () => {
    const html = render(
      <ConfirmDialog
        title="Load anyway?"
        message="This model has a problem on this machine:"
        reasons={["needs 40.0 GB of disk; only 5.0 GB free", "6.4 GB swapped out"]}
        confirmLabel="Load anyway"
      />,
    );
    expect(html).toContain('role="alertdialog"');
    expect(html).toContain("Load anyway?");
    expect(html).toContain("<li>needs 40.0 GB of disk; only 5.0 GB free</li>");
    expect(html).toContain("<li>6.4 GB swapped out</li>");
    expect(html).toContain("Load anyway</button>");
  });

  test("ask() puts a question in front of the host", async () => {
    const answer = ask({ title: "Delete org/model-4bit?", tone: "danger", confirmLabel: "Delete" });
    const html = render(<ConfirmHost />);
    expect(html).toContain("Delete org/model-4bit?");
    // the promise stays pending until the person answers, which is the point
    expect(answer instanceof Promise).toBe(true);
  });

  test("no question means no host at all", () => {
    expect(render(<ConfirmHost />)).toBe("");
  });
});
