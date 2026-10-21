import { matchesModel } from "../api/roles.js";

/**
 * Curated candidates per role: models this build's runtimes can actually load,
 * under the 34 GB ceiling, best first, with each role's shipped default first.
 * `gb` is the download size. `recommendedFor` adds a newly configured default
 * the list does not know yet, so this table cannot hide what the build runs.
 */
export const RECOMMENDED = {
  chat: [
    { model: "qwen2.5:0.5b", gb: 0.4, note: "shipped default — smallest; a quick start or a tiny machine" },
    { model: "qwen2.5:3b", gb: 1.9, note: "next step up — noticeably better instructions" },
    { model: "qwen3.8:27b", gb: 17.7, note: "best all-round quality" },
    { model: "gemma4:26b", gb: 18.7, note: "Mixture-of-Experts, fast for its size" },
    { model: "gpt-oss:20b", gb: 13.8, note: "strong reasoning" },
    { model: "qwen3-coder:30b", gb: 18.6, note: "code and tool calls" },
    { model: "gemma4:12b", gb: 8.0, note: "smallest of the fast ones" },
  ],
  decide: [
    { model: "hf.co/mradermacher/OneJev1.5-0.8B-GGUF:Q8_0", gb: 1.2, note: "shipped default" },
    { model: "nimble", gb: 9.5, note: "best answer accuracy" },
    { model: "clef-flash", gb: 10.6, note: "lowest latency" },
    { model: "tev1:4b", gb: 4.6, note: "strong at tool routing" },
    { model: "laya", gb: 0.8, note: "tiny and quickest to answer" },
  ],
  embed: [
    { model: "embeddinggemma-2:270m", gb: 0.3, note: "shipped default — smallest" },
    { model: "embeddinggemma-2", gb: 1.3, note: "multimodal, long context" },
    { model: "qwen3-embedding:0.6b", gb: 0.6, note: "small, good quality" },
    { model: "bge-m3", gb: 1.2, note: "multilingual" },
    { model: "qwen3-embedding:8b", gb: 4.7, note: "best quality; 4096-dim vectors" },
  ],
  stt: [
    { model: "mlx-community/parakeet-tdt-0.6b-v3", gb: 2.5, note: "shipped default — best accuracy" },
    { model: "mlx-community/parakeet-tdt_ctc-110m", gb: 0.5, note: "small" },
    { model: "mlx-community/parakeet-tdt-0.6b-v2", gb: 2.5, note: "previous version" },
  ],
  tts: [
    { model: "mlx-community/Kokoro-82M-8bit", gb: 0.4, note: "shipped default — near-identical to reference" },
    { model: "mlx-community/Kokoro-82M-6bit", gb: 0.4, note: "smaller still" },
    { model: "mlx-community/Kokoro-82M-4bit", gb: 0.7, note: "most compressed" },
    { model: "prince-canuma/Kokoro-82M-4bit", gb: 0.6, note: "previous default" },
    { model: "mlx-community/Kokoro-82M-bf16", gb: 0.4, note: "reference quality" },
  ],
  image: [
    { model: "unsloth/Qwen-Image-2.1-Turbo-GGUF", gb: 25.9, note: "shipped default — Q8_0 transformer (7.6 GB) plus the base text encoder and VAE" },
    { model: "Qwen/Qwen-Image-2.1-Turbo", gb: 32.5, note: "the same checkpoint at full precision" },
    { model: "Qwen/Qwen-Image-2.1", gb: 33.1, note: "full quality, more steps" },
  ],
  video: [
    { model: "baa-ai/LTX-2.3-22B-RAM-12GB-MLX", gb: 19.1, note: "shipped default — fits the 34 GB cap, low RAM" },
    { model: "notapalindrome/ltx23-mlx-av-q4", gb: 22.8, note: "fits the cap, with audio" },
  ],
};

/** The role's candidates, with its live default first when the list does not already carry it. */
export function recommendedFor(role, defaultModel) {
  const rows = RECOMMENDED[role] ?? [];
  if (!defaultModel) return rows;
  if (rows.some((row) => matchesModel(row.model, defaultModel))) return rows;
  return [{ model: defaultModel, gb: null, note: "current default" }, ...rows];
}
