/**
 * The system pages fetch their data in effects, which never run without a DOM,
 * so their whole-page render above is only the loading state. These render the
 * table components directly against fixtures — the code that actually decides
 * what the admin sees.
 */
import { beforeEach, describe, expect, test } from "bun:test";
import { render } from "preact-render-to-string";

const ModelsModule = await import("../src/tabs/Models.jsx");
const { RoleSetup, GlobalControls, Search, Install, DownloadedList, Memory, GatedNotice } = ModelsModule;
const Models = ModelsModule.default;
const { RoleCard } = await import("../src/components/RoleCard.jsx");
const { runtime } = await import("../src/api/roles.js");
const { WorkerRows, ModelRows } = await import("../src/tabs/Health.jsx");
const { models } = await import("../src/api/models.js");
const { gb } = await import("../src/lib/format.js");

const noop = () => {};

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
    const html = render(<RoleSetup role="chat" entry={READY_ENTRY} pinned={false} onChanged={noop} />);
    expect(html).toContain("qwen2.5:3b");
    expect(html).toContain("Load");
    expect(html).toContain("Unload");
    expect(html).toContain("Keep warm");
    expect(html).toContain("Delete");
    expect(html).toContain(gb(1_900_000_000));
    // parameters and quantization ride along with the size
    expect(html).toContain("3B · Q4_K_M");
    expect(html).toContain("install a model");
    expect(html).toContain("Install");
  });

  test("a missing default names the reason and disables the warm switch", () => {
    const html = render(<RoleSetup role="decide" entry={MISSING_ENTRY} pinned={false} onChanged={noop} />);
    expect(html).toContain("is not installed; install it first");
    expect(html).toContain("Nothing downloaded for this role yet.");
    expect(html).toContain("Keep warm");
    expect(html).toContain("disabled");
  });

  test("the downloaded list covers every model with a delete each", () => {
    const html = render(
      <DownloadedList role="chat" installed={READY_ENTRY.installed} onChanged={noop} />,
    );
    expect(html).toContain("qwen2.5:3b");
    expect(html).toContain("Delete");
  });

  test("search is per role and install shows the runner hint", () => {
    expect(render(<Search role="chat" onPick={noop} />)).toContain("blank: most downloaded");
    // the pump that follows a picked model is an effect, so it is not exercised here
    const html = render(<Install role="chat" runner="ollama" initial={null} onInstalled={noop} />);
    expect(html).toContain("Check");
    expect(html).toContain("Install");
    expect(html).toContain("hf.co/user/repo-GGUF:Q4_K_M");
    expect(html).toContain("Ollama: a library name");
  });
});

describe("a gated model", () => {
  test("shows accept-terms link and retry button", () => {
    const html = render(<GatedNotice model="dgrauet/ltx-2.5-mlx-q4" acceptUrl="https://huggingface.co/dgrauet/ltx-2.5-mlx-q4" busy={false} onRetry={noop} />);
    expect(html).toContain("dgrauet/ltx-2.5-mlx-q4");
    expect(html).toContain("Accept terms on Hugging Face");
    expect(html).toContain("https://huggingface.co/dgrauet/ltx-2.5-mlx-q4");
    expect(html).toContain("Retry install");
  });
});

describe("the Models section", () => {
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

describe("the sidebar card", () => {
  const card = (setupState, setupReason = "") => {
    runtime.value = {
      ready: true,
      error: "",
      warnings: [],
      pins: [],
      roles: {
        chat: {
          label: "Chat",
          endpoint: "POST /v1/chat/completions",
          runner: "ollama",
          color: "#0a84ff",
          role: "chat",
          model: MISSING_ENTRY.default,
          loaded: false,
          pinned: false,
          runningModel: null,
          memoryBytes: 0,
          status: "idle",
          setupState,
          setupReason,
        },
      },
    };
    return render(<RoleCard role="chat" active={false} onOpen={noop} onSetup={noop} />);
  };

  test("a role whose default cannot run shows Setup instead of the switch", () => {
    const html = card("missing", MISSING_ENTRY.reason);
    expect(html).toContain("Setup");
    expect(html).not.toContain('role="switch"');
  });

  test("the card marks the problem with a badge, never error text", () => {
    const html = card("missing", MISSING_ENTRY.reason);
    // the badge flags it and carries the words for its tooltip; the card prints no error paragraph
    expect(html).toContain("needs attention");
    expect(html).not.toContain('role="alert"');
  });

  test("a ready role keeps its warm switch", () => {
    const html = card("ready");
    expect(html).toContain('role="switch"');
    expect(html).not.toContain(">Setup<");
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
    expect(html).toContain("stopped");
    expect(html).toContain("12.5");
  });

  test("says so while memory is still unread, and with nothing loaded", () => {
    expect(render(<Memory memory={null} roles={CATALOG.roles} onChanged={noop} />)).toContain("Reading memory…");
    const empty = { ...MEMORY, ollama: [], warnings: [], workers: {} };
    expect(render(<Memory memory={empty} roles={CATALOG.roles} onChanged={noop} />)).toContain("none loaded");
  });
});

describe("the Health page's tables", () => {
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
