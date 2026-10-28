/**
 * The system pages fetch their data in effects, which never run without a DOM,
 * so their whole-page render above is only the loading state. These render the
 * table components directly against fixtures — the code that actually decides
 * what the admin sees.
 */
import { beforeEach, describe, expect, test } from "bun:test";
import { render } from "preact-render-to-string";

const ModelsModule = await import("../src/tabs/Models.jsx");
const { GlobalControls, Find, ModelList, Memory, GatedNotice, orderRoles } = ModelsModule;
const { LogsCard } = await import("../src/components/LogsCard.jsx");
const { HealthCard } = await import("../src/components/HealthCard.jsx");
const { health } = await import("../src/api/health.js");
const Models = ModelsModule.default;
const { RoleModelCard, Tunable } = await import("../src/components/RoleModelCard.jsx");
const { FindAndInstall, InstallProgress, criteriaFor } = await import("../src/components/FindAndInstall.jsx");
const { runtime, ROLE_META, downloads, installs, downloadFor, pollDownloads, specs } = await import("../src/api/roles.js");
const { WorkerRows, ModelRows } = ModelsModule;
const { models } = await import("../src/api/models.js");
const { gb } = await import("../src/lib/format.js");

const noop = () => {};

// What `/admin/models/status` says about each role, trimmed to the fields the views read.
const spec = (runner, engine, kind, family, example, recommended) => ({ runner, engine, kind, family, accepts: `e.g. ${example}`, example, recommended });
specs.value = {
  chat: spec("ollama", "ollama", "GGUF · text generation", null, "hf.co/user/repo-GGUF:Q4_K_M", [
    { model: "qwen2.5:3b", gb: 1.9, note: "next step up", installed: true, blockers: [] },
    { model: "qwen3.8:27b", gb: 17.7, note: "best all-round quality", installed: false, blockers: ["17.7 GB of weights but only 10.0 GB RAM free now"] },
  ]),
  embed: spec("ollama", "ollama", "GGUF · feature extraction", null, "hf.co/user/repo-GGUF:Q8_0", []),
  decide: spec("huggingface", "onnxruntime", "ONNX · Vega typed decisions", "vega", "nvkudva/vega-onnx-q8", []),
  stt: spec("huggingface", "onnx-asr", "ONNX · Parakeet speech-to-text", "parakeet", "istupakov/parakeet-tdt-0.6b-v2-onnx", []),
  tts: spec("huggingface", "onnxruntime", "ONNX · Kokoro text-to-speech", "kokoro", "onnx-community/Kokoro-82M-v1.0-ONNX", []),
  image: spec("huggingface", "diffusers", "Diffusers · Qwen-Image-2", "qwen-image-2", "Qwen/Qwen-Image-2.1", []),
  video: spec("huggingface", "diffusers", "GGUF · Wan / LTX-Video", "wan or ltx", "QuantStack/Wan2.2-TI2V-5B-GGUF", []),
};
specs.value.chat.default = "qwen2.5:3b";
specs.value.chat.accepts = "Ollama: a library name (qwen2.5vl:7b) or any GGUF repo on Hugging Face as hf.co/user/repo-GGUF:Q4_K_M.";

const CATALOG = {
  roles: {
    chat: { default: "qwen2.5:3b", runner: "ollama" },
    image: { default: "Qwen/Qwen-Image-2.1", runner: "huggingface" },
  },
  installed: {
    ollama: [{ name: "qwen2.5:3b", size_bytes: 1_900_000_000, parameters: "3B", quantization: "Q4_K_M" }],
    huggingface: [{ name: "Qwen/Qwen-Image-2.1", size_bytes: 23_000_000_000 }],
  },
};

const READY_ENTRY = {
  default: "qwen2.5:3b",
  runner: "ollama",
  installed: [{ name: "qwen2.5:3b", size_bytes: 1_900_000_000, parameters: "3B", quantization: "Q4_K_M" }],
  state: "ready",
  reason: null,
  total_bytes: 1_900_000_000,
};

const MISSING_ENTRY = {
  default: "hf.co/mradermacher/OneJev1.5-0.8B-GGUF:Q8_0",
  runner: "ollama",
  installed: [],
  recommended: [{ model: "hf.co/mradermacher/OneJev1.5-0.8B-GGUF:Q8_0", gb: 0.9, note: "current default", installed: false, blockers: [] }],
  state: "missing",
  reason: "hf.co/mradermacher/OneJev1.5-0.8B-GGUF:Q8_0 is not installed; install it first",
  total_bytes: 0,
};

const MEMORY = {
  total_bytes: 68_719_476_736,
  available_bytes: 10_000_000_000,
  swap_used_bytes: 9_400_000_000,
  disk_free_bytes: 100_000_000_000,
  ollama: [{ name: "qwen2.5:3b", size_bytes: 1_900_000_000, vram_bytes: 1_500_000_000 }],
  cpu_percent: 12.5,
  cpu_count: 10,
  load_average: [1.2, 1.0, 0.8],
  workers: {
    image: { status: "ready", pid: 11, restarts: 0, rss_bytes: 2_000_000_000, cpu_percent: 3.5, held: false, model: "Qwen/Qwen-Image-2.1", loaded: true },
    video: { status: "stopped", pid: null, restarts: 2, rss_bytes: 0, cpu_percent: 0, held: true, model: "", loaded: false },
  },
  warnings: ["9.4 GB swapped out: models will run slowly"],
};

describe("a role's setup card", () => {
  test("a ready role shows its current model, load controls, downloads and storage", () => {
    const html = render(<RoleModelCard density="detail" role="chat" entry={READY_ENTRY} pinned={false} onChanged={noop} />);
    expect(html).toContain("qwen2.5:3b");
    expect(html).toContain("Load");
    expect(html).toContain("qwen2.5:3b is the default");
    // the keep-warm control is a switch in the card header, not a text button
    expect(html).toContain('role="switch"');
    expect(html).toContain("keep warm Chat");
    expect(html).toContain("Delete");
    expect(html).toContain(gb(1_900_000_000));
    // parameters and quantization ride along with the size
    expect(html).toContain("3B · Q4_K_M");
    expect(html).toContain("Add model");
    expect(html).not.toContain("Find &amp; install");
  });

  test("a missing default names the reason and disables the warm switch", () => {
    const html = render(<RoleModelCard density="detail" role="decide" entry={MISSING_ENTRY} pinned={false} onChanged={noop} />);
    expect(html).toContain("Download</button>");
    expect(html).toContain('role="switch"');
    expect(html).toContain("disabled");
  });

  test("every card carries a health button for its runtime details", () => {
    const html = render(<RoleModelCard density="detail" role="chat" entry={READY_ENTRY} pinned={false} onChanged={noop} />);
    expect(html).toContain("Chat health details");
  });

  test("the tts card carries its default voice tunable", () => {
    const html = render(
      <RoleModelCard density="detail" role="tts" entry={READY_ENTRY} pinned={false} tunables={{ "tts.voice": "af_heart" }} onChanged={noop} onSaved={noop} />,
    );
    expect(html).toContain("Default voice");
    expect(html).toContain('value="af_heart"');
  });

  test("a worker role's card has a start/stop switch and a keep-warm switch inside Settings", () => {
    const html = render(<RoleModelCard density="detail" role="image" entry={READY_ENTRY} pinned onChanged={noop} />);
    expect(html).toContain("<h4>Settings</h4>");
    expect(html).toContain(">Stop<");
    expect(html).toContain('aria-label="stop keeping warm Image"');
    expect(html).toContain(">Stop<");
    expect(html).toContain(">Restart<");
    expect(html).not.toContain(">Start<");
  });

  test("a stopped worker's header offers Start, never Stop or Restart", () => {
    runtime.value = { ...runtime.value, roles: { image: { status: "stopped" } } };
    const html = render(<RoleModelCard density="detail" role="image" entry={READY_ENTRY} pinned onChanged={noop} />);
    runtime.value = { ready: false, error: "", roles: {}, warnings: [], memory: null, pins: [] };
    expect(html).toContain(">Start<");
    expect(html).not.toContain(">Stop<");
    expect(html).not.toContain(">Restart<");
  });

  test("an Ollama role's card keeps one keep-warm switch and no Settings box", () => {
    const html = render(<RoleModelCard density="detail" role="chat" entry={READY_ENTRY} pinned={false} onChanged={noop} />);
    expect(html).not.toContain("<h4>Settings</h4>");
    expect(html).not.toContain('aria-label="start Chat"');
    expect(html.match(/role="switch"/g)).toHaveLength(1);
  });

  test("the downloaded list covers every model with a delete each", () => {
    const html = render(
      <ModelList role="chat" installed={READY_ENTRY.installed} onChanged={noop} />,
    );
    expect(html).toContain("qwen2.5:3b");
    expect(html).toContain("Delete");
  });

  test("one box searches per role and installs, with the runner hint", () => {
    const html = render(<Find role="chat" onInstalled={noop} />);
    expect(html).toContain("Search Hugging Face");
    expect(html).toContain("Install");
    expect(html).toContain("hf.co/user/repo-GGUF:Q4_K_M");
    expect(html).toContain("Ollama: a library name");
  });
});

describe("a gated model", () => {
  test("shows accept-terms link and retry button", () => {
    const html = render(<GatedNotice model="meta-llama/Llama-3.1-8B-Instruct" acceptUrl="https://huggingface.co/meta-llama/Llama-3.1-8B-Instruct" busy={false} onRetry={noop} />);
    expect(html).toContain("meta-llama/Llama-3.1-8B-Instruct");
    expect(html).toContain("Accept terms on Hugging Face");
    expect(html).toContain("https://huggingface.co/meta-llama/Llama-3.1-8B-Instruct");
    expect(html).toContain("Retry install");
  });
});


describe("the Find and Install popup", () => {
  test("one popup, criteria follow the role: GGUF for chat, parakeet family for stt", () => {
    const chat = render(<FindAndInstall role="chat" onClose={noop} onInstalled={noop} />);
    expect(chat).toContain('role="dialog"');
    expect(chat).toContain("Find &amp; install — Chat");
    expect(chat).toContain("GGUF · text generation");
    expect(chat).toContain("hf.co/user/repo-GGUF:Q4_K_M");
    expect(chat).not.toContain("parakeet");
    const stt = render(<FindAndInstall role="stt" onClose={noop} onInstalled={noop} />);
    expect(stt).toContain("Find &amp; install — Speech to text");
    expect(stt).toContain("ONNX · Parakeet speech-to-text");
    expect(stt).toContain("parakeet");
    expect(stt).toContain("istupakov/parakeet-tdt-0.6b-v2-onnx");
  });

  test("a recommended table lists the server's candidates with their download state and fit", () => {
    const html = render(<FindAndInstall role="chat" onClose={noop} onInstalled={noop} />);
    expect(html).toContain("Recommended");
    expect(html).toContain("qwen3.8:27b");
    expect(html).toContain("17.7 GB");
    expect(html).toContain("Download</button>");
    expect(html).toContain("tight");
    expect(html).toContain("only 10.0 GB RAM free");
    // the configured default is marked as such
    expect(html).toContain("default");
  });

  test("criteria come from the server's role specs, and an unknown role opens nothing", () => {
    expect(criteriaFor("chat").kind).toContain("GGUF");
    expect(criteriaFor("stt").family).toBe("parakeet");
    expect(criteriaFor("image").family).toBe("qwen-image-2");
    expect(criteriaFor("nope").kind).toBe("Hugging Face repo");
    expect(render(<FindAndInstall role={null} onClose={noop} onInstalled={noop} />)).toBe("");
  });
});

describe("the model settings popup", () => {
  test("one dialog per role, loading its settings card", async () => {
    const { ModelSettingsPopup } = await import("../src/components/ModelSettings.jsx");
    const html = render(<ModelSettingsPopup role="chat" onClose={noop} />);
    expect(html).toContain('role="dialog"');
    expect(html).toContain("chat model settings");
    expect(html).toContain("loading model settings…");
    expect(render(<ModelSettingsPopup role={null} onClose={noop} />)).toBe("");
  });
});

describe("the Logs card", () => {
  test("starts minimized, naming the role's log file, with no terminal shown", () => {
    const html = render(<LogsCard role="video" />);
    expect(html).toContain("Logs");
    expect(html).toContain("video.log");
    expect(html).toContain('aria-expanded="false"');
    expect(html).not.toContain("<pre");
  });

  test("an Ollama role names its own request log, not the engine's", () => {
    expect(render(<LogsCard role="chat" />)).toContain("chat requests");
  });
});

describe("the Health card", () => {
  test("its own card, naming the role and the worker's runtime", () => {
    health.value = { workers: { image: MEMORY.workers.image } };
    const html = render(<HealthCard role="image" label="Image" />);
    expect(html).toContain("Health");
    expect(html).toContain("Image");
    expect(html).toContain("ready");
    expect(html).toContain("Qwen/Qwen-Image-2.1");
  });

  test("an Ollama role points at the server card", () => {
    health.value = { workers: {} };
    expect(render(<HealthCard role="chat" label="Chat" />)).toContain("Served by Ollama");
  });

  beforeEach(() => {
    health.value = null;
  });
});

describe("the Models section", () => {
  test("role cards follow the mini tabs' order, not the API's", () => {
    const apiOrder = { stt: 1, chat: 2, video: 3, embed: 4, tts: 5, decide: 6, image: 7 };
    expect(orderRoles(apiOrder).map(([role]) => role)).toEqual(["chat", "decide", "embed", "image", "video", "stt", "tts"]);
  });

  test("it starts loading without needing an active prop", () => {
    // Settings mounts the page with no props; effects never run in SSR, so only the loading state renders
    const html = render(<Models />);
    expect(html).toContain("Models");
    expect(html).toContain("loading models…");
  });
});

describe("the global worker controls", () => {
  test("restart all, stop all and restart ollama are offered", () => {
    const html = render(<GlobalControls managed onChanged={noop} />);
    expect(html).toContain("Restart all");
    expect(html).toContain("Stop all");
    expect(html).toContain("Restart Ollama");
  });

  test("restart ollama is disabled when omni does not manage it", () => {
    const html = render(<GlobalControls managed={false} onChanged={noop} />);
    expect(html).toMatch(/disabled[^>]*>Restart Ollama/);
  });
});

describe("a tunable setting", () => {
  const choices = {
    key: "gateway.idle_unload_minutes",
    label: "Idle unload",
    hint: "Unload a worker's model after this long unused.",
    number: true,
    options: [
      { value: 0, label: "Never" },
      { value: 10, label: "10 minutes" },
      { value: 20, label: "20 minutes" },
    ],
  };

  test("a fixed choice is a dropdown with no Save button", () => {
    const html = render(<Tunable spec={choices} stored={10} onSaved={noop} />);
    expect(html).toContain("<select");
    expect(html).toContain("Never");
    expect(html).toContain("20 minutes");
    expect(html).not.toContain(">Save<");
  });

  test("a stored value outside the choices is shown, not silently reset", () => {
    const html = render(<Tunable spec={choices} stored={42} onSaved={noop} />);
    expect(html).toContain("42 minutes");
  });

  test("a free number still needs the Save button", () => {
    const spec = { key: "image.steps", label: "Image steps", hint: "", number: true, step: 1 };
    const html = render(<Tunable spec={spec} stored={4} onSaved={noop} />);
    expect(html).not.toContain("<select");
    expect(html).toContain(">Save<");
  });
});

describe("the sidebar card", () => {
  // A card reads one status: what the runtime is doing, in the person's words, with the one action
  // that follows from it. Each case here is a state a real gateway reports.
  const BASE = {
    model: MISSING_ENTRY.default,
    loaded: false,
    pinned: false,
    runningModel: null,
    memoryBytes: 0,
    status: "idle",
    setupState: "ready",
    setupReason: "",
  };

  const sidebar = (role, state = {}, props = {}) => {
    runtime.value = {
      ready: true,
      error: "",
      warnings: [],
      pins: [],
      roles: { [role]: { ...ROLE_META[role], role, ...BASE, ...state } },
    };
    return render(<RoleModelCard density="mini" role={role} active={false} onOpen={noop} onSetup={noop} {...props} />);
  };

  const blocked = { setupState: "missing", setupReason: MISSING_ENTRY.reason };

  test("a role whose default cannot run shows a setup action instead of the switch", () => {
    const html = sidebar("chat", blocked);
    expect(html).toContain("Set up Chat");
    expect(html).not.toContain('role="switch"');
  });

  test("the card marks the problem with a badge, never error text", () => {
    const html = sidebar("chat", blocked);
    // the badge flags it and carries the words for its tooltip; the card prints no error paragraph
    expect(html).toContain("needs attention");
    expect(html).not.toContain('role="alert"');
  });

  test("a role that needs setup says so, not 'stopped'", () => {
    expect(sidebar("chat", blocked)).toContain(">needs setup<");
  });

  test("a ready role keeps its warm switch", () => {
    const html = sidebar("chat", { status: "ready" });
    expect(html).toContain('role="switch"');
    expect(html).not.toContain("Set up Chat");
  });

  test("a worker that is up but holding no model reads 'not running', never 'running'", () => {
    // The old card took its word from the process and its light from memory, so a live tts worker
    // with an unloaded model said "running" beside a red dot.
    const html = sidebar("tts", { status: "ready" });
    expect(html).toContain(">idle · not loaded<");
    expect(html).not.toContain(">loaded<");
    expect(html).toContain(">Stop<");
  });

  test("a stopped worker's card offers Start", () => {
    const html = sidebar("tts", { status: "stopped" });
    expect(html).toContain(">off<");
    expect(html).toContain("Start the Text to speech worker");
    expect(html).toContain(">Start<");
    expect(html).not.toContain('role="switch"');
  });

  test("a running worker's card offers Stop", () => {
    const html = sidebar("tts", { status: "ready" });
    expect(html).toContain("Stop the Text to speech worker");
    expect(html).toContain(">Stop<");
    expect(html).not.toContain(">Start<");
    expect(html).not.toContain('role="switch"');
  });

  test("a failed worker offers Start too", () => {
    const html = sidebar("image", { status: "failed" });
    expect(html).toContain(">failed<");
    expect(html).toContain("Start the Image worker");
  });

  test("an unreachable Ollama reads stopped and points at the server controls", () => {
    const html = sidebar("chat", { status: "unavailable", engineUrl: "127.0.0.1:11434" });
    expect(html).toContain(">off<");
    expect(html).toContain("Open the server controls");
    expect(html).toContain("127.0.0.1:11434");
    // nothing here can start an Ollama omni does not own, so no switch and no Start
    expect(html).not.toContain('role="switch"');
    expect(html).not.toContain("Start the Chat worker");
  });

  test("warm and running differ only in whether the model is held", () => {
    const warm = sidebar("chat", { status: "loaded", loaded: true, pinned: true, memoryBytes: 2_000_000_000 });
    expect(warm).toContain(">loaded · kept<");
    expect(warm).toContain('aria-checked="true"');

    const running = sidebar("chat", { status: "loaded", loaded: true, memoryBytes: 2_000_000_000 });
    expect(running).toContain(">loaded<");
    expect(running).toContain('aria-checked="false"');
  });

  test("a pinned role still loading says so", () => {
    const html = sidebar("chat", { pinned: true, status: "idle" });
    expect(html).toContain(">loading model…<");
    expect(html).not.toContain(">idle · not loaded<");
  });

  test("mini density shows no detail controls, detail header mirrors mini", () => {
    const mini = sidebar("chat", { status: "ready" });
    expect(mini).toContain("/v1/chat/completions");
    expect(mini).not.toContain("Find &amp; install a model");
    expect(mini).not.toContain("Nothing downloaded for this role yet.");
    const detail = render(<RoleModelCard density="detail" role="chat" entry={READY_ENTRY} pinned onChanged={noop} />);
    expect(detail).toContain("Add model");
    expect(detail).not.toContain("Find &amp; install");
    // same colourful header as mini: role colour badge, label, endpoint, warm switch
    expect(detail).toContain("--role-color:var(--role-chat)");
    expect(detail).toContain("Chat");
    expect(detail).toContain("/v1/chat/completions");
    expect(detail).toContain("warm");
  });

  beforeEach(() => {
    runtime.value = { ready: false, error: "", roles: {}, warnings: [], memory: null, pins: [] };
  });
});

describe("the memory panel", () => {
  test("reports RAM, CPU, Ollama's residents and the workers", () => {
    const html = render(<Memory memory={MEMORY} roles={CATALOG.roles} onChanged={noop} />);
    expect(html).toContain(gb(MEMORY.total_bytes));
    expect(html).toContain("9.4 GB swapped out: models will run slowly");
    expect(html).toContain("Loaded in Ollama");
    expect(html).toContain("qwen2.5:3b");
    expect(html).toContain("12.5");
    // worker start/stop lives on the role cards, not here
    expect(html).not.toContain("<h3>Workers</h3>");
  });

  test("says so while memory is still unread, and with nothing loaded", () => {
    expect(render(<Memory memory={null} roles={CATALOG.roles} onChanged={noop} />)).toContain("Reading memory…");
    const empty = { ...MEMORY, ollama: [], warnings: [], workers: {} };
    expect(render(<Memory memory={empty} roles={CATALOG.roles} onChanged={noop} />)).toContain("none loaded");
  });
});

describe("the health tables", () => {
  test("worker rows show the model a worker holds", () => {
    const html = render(
      <table><tbody>
        <WorkerRows workers={MEMORY.workers} />
      </tbody></table>,
    );
    expect(html).toContain("image");
    expect(html).toContain("Qwen/Qwen-Image-2.1");
    // the stopped worker holds nothing, so no model is claimed
    expect(html).toContain("—");
    expect(html).toContain("held");
  });

  test("model rows mark the default", () => {
    models.value = {
      data: [
        { id: "qwen2.5:3b", role: "chat" },
        { id: "other:1b", role: "chat" },
      ],
      defaults: { chat: "qwen2.5:3b" },
    };
    const html = render(<table><tbody><ModelRows /></tbody></table>);
    expect(html).toMatch(/qwen2\.5:3b\s+\(default\)/);
    expect(html).not.toMatch(/other:1b\s+\(default\)/);
  });

  beforeEach(() => {
    models.value = { data: [], defaults: {} };
  });
});

describe("download progress from any source", () => {
  beforeEach(() => {
    installs.value = {};
    downloads.value = {};
  });

  test("a gateway-reported download shows a progress bar with no local install", () => {
    downloads.value = {
      stt: { model: "mlx-community/parakeet-tdt-0.6b-v3", busy: true, status: "downloading", fraction: 0.25, warnings: [], error: "" },
    };
    const html = render(<InstallProgress role="stt" />);
    expect(html).toContain('role="progressbar"');
    expect(html).toContain("downloading");
  });

  test("a busy local install wins over the gateway report", () => {
    installs.value = {
      stt: { model: "mlx-community/parakeet-tdt-0.6b-v3", busy: true, status: "working…", fraction: 0, warnings: [], error: "" },
    };
    downloads.value = {
      stt: { model: "mlx-community/parakeet-tdt-0.6b-v3", busy: true, status: "downloading", fraction: 0.5, warnings: [], error: "" },
    };
    expect(downloadFor("stt").status).toBe("working…");
  });

  test("nothing renders when nothing is downloading", () => {
    expect(render(<InstallProgress role="stt" />)).toBe("");
  });

  test("a download that failed while the browser was closed surfaces on reopen", async () => {
    const realFetch = globalThis.fetch;
    globalThis.fetch = async () => ({
      ok: true,
      json: async () => ({
        downloads: [],
        recent: [{ role: "stt", model: "mlx-community/parakeet-tdt-0.6b-v3", error: "boom", finished_at: 1 }],
      }),
    });
    try {
      await pollDownloads();
    } finally {
      globalThis.fetch = realFetch;
    }
    expect(installs.value.stt?.error).toBe("boom");
    expect(render(<InstallProgress role="stt" />)).toContain("boom");
  });

  test("an old failure never overwrites a later successful install", async () => {
    const realFetch = globalThis.fetch;
    globalThis.fetch = async () => ({
      ok: true,
      json: async () => ({
        downloads: [],
        recent: [{ role: "image", model: "old/model", error: "old failure", finished_at: 2 }],
      }),
    });
    try {
      await pollDownloads();
      installs.value = { ...installs.value, image: { model: "new/model", busy: false, status: "installed new/model", error: "" } };
      await pollDownloads();
    } finally {
      globalThis.fetch = realFetch;
    }
    expect(installs.value.image.error).toBe("");
  });
});
