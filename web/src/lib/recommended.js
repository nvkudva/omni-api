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
    { model: "nvkudva/vega-onnx-q8", gb: 0.93, note: "shipped default — Vega 0.8B, 8-bit ONNX; calibrated, with prediction sets and abstain" },
  ],
  embed: [
    { model: "embeddinggemma-2:270m", gb: 0.3, note: "shipped default — smallest" },
    { model: "embeddinggemma-2", gb: 1.3, note: "multimodal, long context" },
    { model: "qwen3-embedding:0.6b", gb: 0.6, note: "small, good quality" },
    { model: "bge-m3", gb: 1.2, note: "multilingual" },
    { model: "qwen3-embedding:8b", gb: 4.7, note: "best quality; 4096-dim vectors" },
  ],
  stt: [
    { model: "istupakov/parakeet-tdt-0.6b-v2-onnx", gb: 0.7, note: "shipped default — English, int8" },
    { model: "istupakov/parakeet-tdt-0.6b-v3-onnx", gb: 0.7, note: "25 European languages, int8" },
  ],
  tts: [
    { model: "onnx-community/Kokoro-82M-v1.0-ONNX", gb: 0.12, note: "shipped default — int8, 27 voices" },
  ],
  image: [
    { model: "unsloth/Qwen-Image-2.1-Turbo-GGUF", gb: 25.9, note: "shipped default — Q8_0 transformer (7.6 GB) plus the base text encoder and VAE" },
    { model: "Qwen/Qwen-Image-2.1-Turbo", gb: 32.5, note: "the same checkpoint at full precision" },
    { model: "Qwen/Qwen-Image-2.1", gb: 33.1, note: "full quality, more steps" },
  ],
  video: [
    { model: "QuantStack/Wan2.2-TI2V-5B-GGUF", gb: 5.4, note: "shipped default — Wan 2.2 5B, Q8_0; text and image to video (+14 GB text encoder and VAE)" },
    { model: "city96/LTX-Video-0.9.6-distilled-gguf", gb: 2.1, note: "LTX-Video 2B distilled, Q8_0; fast, 8 steps (+21 GB text encoder and VAE)" },
    { model: "QuantStack/LTXV-13B-0.9.8-distilled-GGUF", gb: 14, note: "LTX-Video 13B distilled, Q8_0; set base = \"Lightricks/LTX-Video-0.9.8-13B-distilled\" in config.toml" },
  ],
};

/** The role's candidates, with its live default first when the list does not already carry it. */
export function recommendedFor(role, defaultModel) {
  const rows = RECOMMENDED[role] ?? [];
  if (!defaultModel) return rows;
  if (rows.some((row) => matchesModel(row.model, defaultModel))) return rows;
  return [{ model: defaultModel, gb: null, note: "current default" }, ...rows];
}
