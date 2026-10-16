/**
 * The system pages fetch their data in effects, which never run without a DOM,
 * so their whole-page render above is only the loading state. These render the
 * table components directly against fixtures — the code that actually decides
 * what the admin sees.
 */
import { beforeEach, describe, expect, test } from "bun:test";
import { render } from "preact-render-to-string";

const { RoleRow, InstalledRows, Search, Install, Memory } = await import("../src/tabs/Models.jsx");
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

describe("the Models page's tables", () => {
  test("a default row lists the installed choices and its size", () => {
    const html = render(
      <table><tbody>
        <RoleRow role="chat" config={CATALOG.roles.chat} installed={CATALOG.installed} onChanged={noop} />
      </tbody></table>,
    );
    expect(html).toContain("ollama");
    expect(html).toContain("qwen2.5:3b");
    expect(html).toContain(gb(1_900_000_000));
    expect(html).toContain("Load");
    expect(html).toContain("Unload");
  });

  test("a default that is not downloaded still appears in the list", () => {
    const html = render(
      <table><tbody>
        <RoleRow
          role="stt"
          config={{ default: "animaslabs/parakeet-tdt-0.6b-v3-mlx-4bit", runner: "huggingface" }}
          installed={CATALOG.installed}
          onChanged={noop}
        />
      </tbody></table>,
    );
    expect(html).toContain("animaslabs/parakeet-tdt-0.6b-v3-mlx-4bit");
    expect(html).toContain("not downloaded");
  });

  test("installed rows cover every runner", () => {
    const html = render(
      <table><tbody>
        <InstalledRows catalog={CATALOG} onChanged={noop} />
      </tbody></table>,
    );
    expect(html).toContain("qwen2.5:3b");
    expect(html).toContain("Qwen/Qwen-Image-2.1");
    // parameters and quantization ride along with the size
    expect(html).toContain("3B · Q4_K_M");
  });

  test("the search panel offers every role", () => {
    const html = render(<Search catalog={CATALOG} onPick={noop} />);
    expect(html).toContain("Search Hugging Face");
    expect(html).toContain("blank for most downloaded");
  });

  test("the install panel offers every role and the runner's hint", () => {
    // the pump that follows a picked model is an effect, so it is not exercised here
    const html = render(<Install catalog={CATALOG} pick={null} onInstalled={noop} />);
    expect(html).toContain("Install");
    expect(html).toContain("hf.co/user/repo-GGUF:Q4_K_M");
    expect(html).toContain("Ollama: a library name");
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
