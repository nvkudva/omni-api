"""Roles, which runtime serves each, and what each runtime can actually load."""
import importlib.util
import re
import sys

from omni import config, schemas

# Everything omni knows about a role's runtime, in one table: the gateway, the workers and the
# playground (through /admin/models/status) all read it, so a role is described exactly once.
# `family`: markers a repo name must carry, at a word start, for the runtime to load it.
# `files`: the files a fixed-layout runtime reads, so a download skips the other quants.
# `deps`: what the worker must import to run at all. `platform`: the one OS it renders on.
# `recommended`: (model, download GB for a first load, note), the shipped default first.
ROLE_SPECS = {
    "chat": {
        "runner": "ollama", "engine": "ollama", "family": (), "files": None, "deps": (), "platform": None,
        "search": {"filter": ["gguf"], "pipeline_tag": "text-generation"},
        "kind": "GGUF · text generation",
        "accepts": "Ollama: a library name (qwen2.5vl:7b) or any GGUF repo on Hugging Face as hf.co/user/repo-GGUF:Q4_K_M.",
        "example": "hf.co/user/repo-GGUF:Q4_K_M",
        "recommended": [
            ("qwen2.5:0.5b", 0.4, "shipped default — smallest; a quick start or a tiny machine"),
            ("qwen3.5:0.8b", 1.2, "small, newer generation"),
            ("gemma4:12b", 8.0, "smallest of the fast ones"),
            ("hf.co/JetBrains/Mellum2.1-12B-A2.5B-Thinking-GGUF:Q4_K_M", 8.1, "Mellum 2.1, 12B MoE (2.5B active) — thinking, code"),
        ],
    },
    "decide": {
        "runner": "huggingface", "engine": "onnxruntime", "family": ("vega", "laya", "gliner2"), "platform": None,
        "files": {
            "vega": ["v2/embed_q8.onnx*", "v2/decoder_q8.onnx*", "v2/engine.onnx", "v2/tokenizer.json",
                     "v2/vega_web.json", "v2/vision_q8.onnx*"],
            "laya": ["rl_agent_config.json", "model.safetensors", "tokenizer/*", "encoder/*"],
            "gliner2": ["*.json", "model.safetensors"],
        },
        "deps": ("onnxruntime", "tokenizers"),
        # what a model other than the shipped Vega needs on top, which only the media extra installs
        "backend_deps": {"laya": ("laya", "torch"), "gliner2": ("gliner2", "torch")},
        "search": {"pipeline_tag": "text-classification"},
        "kind": "Typed decisions · Vega (ONNX), Laya and GLiNER2.5-Decide (transformers)",
        "accepts": "A Vega ONNX repo with the v2 layout, a Laya checkpoint repo or a GLiNER2.5-Decide repo, or a Hugging Face page URL, e.g. nvkudva/vega-onnx-q8. Downloads to the HF cache.",
        "example": "nvkudva/vega-onnx-q8",
        "recommended": [
            ("nvkudva/vega-onnx-q8", 0.93, "shipped default — Vega 0.8B, 8-bit ONNX; calibrated, with prediction sets and abstain"),
            ("convaiinnovations/laya", 0.85, "Laya, 421M — English; answers every question in one pass, with its own act/escalate head"),
            ("convaiinnovations/laya-multilingual", 0.65, "Laya multilingual, 322M — 100+ languages, reads up to 1,024 tokens"),
            ("fastino/GLiNER2.5-Decide", 1.95, "GLiNER2.5-Decide, 340M — English label-set classifier; reads text only"),
        ],
    },
    "embed": {
        "runner": "ollama", "engine": "ollama", "family": (), "files": None, "deps": (), "platform": None,
        "search": {"filter": ["gguf"], "pipeline_tag": "feature-extraction"},
        "kind": "GGUF · feature extraction",
        "accepts": "Ollama: a library name (embeddinggemma-2) or any GGUF repo on Hugging Face as hf.co/user/repo-GGUF:Q8_0.",
        "example": "hf.co/user/repo-GGUF:Q8_0",
        "recommended": [
            ("embeddinggemma-2", 1.3, "shipped default — multimodal, long context"),
            ("embeddinggemma-2:270m", 0.3, "smallest"),
            ("qwen3-embedding:0.6b", 0.6, "small, good quality"),
            ("bge-m3", 1.2, "multilingual"),
            ("qwen3-embedding:8b", 4.7, "best quality; 4096-dim vectors"),
        ],
    },
    "stt": {
        "runner": "huggingface", "engine": "onnx-asr", "family": ("parakeet", "moonshine", "whisper", "oruk"),
        "platform": None,
        # Parakeet runs as int8 ONNX; the others load through transformers (`*.json` covers their tokenizer and
        # processor files, and oruk also needs its own modeling code, so it names `*.py`).
        "files": {
            "parakeet": ["config.json", "vocab.txt", "encoder-model.int8.onnx", "decoder_joint-model.int8.onnx"],
            "moonshine": ["*.json", "*.safetensors"],
            "whisper": ["*.json", "*.safetensors"],
            "oruk": ["*.json", "*.safetensors", "*.py"],
        },
        "deps": ("onnxruntime", "onnx_asr", "transformers", "torch"),
        "search": {"pipeline_tag": "automatic-speech-recognition"},
        "kind": "Speech-to-text · Parakeet (ONNX), Moonshine, Whisper, oruk (transformers)",
        "accepts": "A Parakeet ONNX repo (with int8 files), or a Moonshine, Whisper or oruk transformers repo, or a Hugging Face page URL, e.g. istupakov/parakeet-tdt-0.6b-v2-onnx. Downloads to the HF cache.",
        "example": "istupakov/parakeet-tdt-0.6b-v2-onnx",
        "recommended": [
            ("istupakov/parakeet-tdt-0.6b-v2-onnx", 0.7, "shipped default — English, int8"),
            ("istupakov/parakeet-tdt-0.6b-v3-onnx", 0.7, "25 European languages, int8"),
            ("UsefulSensors/moonshine-tiny", 0.11, "Moonshine Tiny, 27M — the smallest; English, fast on CPU"),
            ("openai/whisper-small", 1.0, "Whisper Small, 244M — about 100 languages"),
            ("oruk/oruk-loop-153M", 0.6, "oruk-loop, 153M — English; runs the repo's own code and needs about 4 GB RAM; free for non-commercial and small-business use"),
        ],
    },
    "tts": {
        "runner": "huggingface", "engine": "onnxruntime", "family": ("kokoro", "kitten", "supertonic"), "platform": None,
        "files": {
            "kokoro": ["config.json", "tokenizer.json", "onnx/model_quantized.onnx", "voices/*.bin"],
            "kitten": ["config.json", "voices.npz", "*.onnx"],
            "supertonic": ["onnx/*.onnx", "onnx/*.json", "voice_styles/*.json"],
        },
        "deps": ("onnxruntime", "misaki", "supertonic"),
        "search": {"pipeline_tag": "text-to-speech"},
        "kind": "Text-to-speech · Kokoro, Kitten TTS, Supertonic (ONNX)",
        "accepts": "A Kokoro, Kitten TTS or Supertonic ONNX repo id or Hugging Face page URL, e.g. onnx-community/Kokoro-82M-v1.0-ONNX. Downloads to the HF cache.",
        "example": "onnx-community/Kokoro-82M-v1.0-ONNX",
        "recommended": [
            ("onnx-community/Kokoro-82M-v1.0-ONNX", 0.12, "shipped default — int8, 27 voices"),
            ("KittenML/kitten-tts-nano-0.8-int8", 0.03, "Kitten TTS Nano, 15M — the smallest; English, 8 voices"),
            ("KittenML/kitten-tts-mini-0.8", 0.08, "Kitten TTS Mini, 80M — English, 8 voices, better quality"),
            ("Supertone/supertonic-3", 0.4, "Supertonic 3 — 31 languages, 10 voices; OpenRAIL licence"),
        ],
    },
    "image": {
        "runner": "huggingface", "engine": "diffusers", "family": ("qwen-image-2", "stable-diffusion-v1-5", "sdxl-lightning"),
        # Qwen reads its whole repo (or a GGUF bundle); the other two read half-precision files only. Lightning's
        # repo holds nothing but UNets, so it also takes the text encoders and VAE of `bases`' checkpoint.
        "files": {
            "stable-diffusion-v1-5": [
                "model_index.json", "scheduler/*", "tokenizer/*", "feature_extractor/*", "text_encoder/config.json",
                "text_encoder/model.fp16.safetensors", "unet/config.json", "unet/diffusion_pytorch_model.fp16.safetensors",
                "vae/config.json", "vae/diffusion_pytorch_model.fp16.safetensors"],
            "sdxl-lightning": ["sdxl_lightning_4step_unet.safetensors"],
        },
        "bases": {
            "sdxl-lightning": ("stabilityai/stable-diffusion-xl-base-1.0", [
                "model_index.json", "scheduler/*", "tokenizer*/*", "text_encoder*/config.json",
                "text_encoder/model.fp16.safetensors", "text_encoder_2/model.fp16.safetensors", "unet/config.json",
                "vae/config.json", "vae/diffusion_pytorch_model.fp16.safetensors"]),
        },
        "deps": ("torch", "diffusers"), "platform": "darwin",
        "search": {"filter": ["diffusers"], "pipeline_tag": "text-to-image"},
        "kind": "Diffusers · Qwen-Image-2, Stable Diffusion 1.5, SDXL-Lightning",
        "accepts": "A Qwen-Image-2, Stable Diffusion 1.5 or SDXL-Lightning repo id or Hugging Face page URL, e.g. Qwen/Qwen-Image-2.1. Downloads to the HF cache.",
        "example": "Qwen/Qwen-Image-2.1",
        "recommended": [
            ("unsloth/Qwen-Image-2.1-Turbo-GGUF", 25.9, "shipped default — Q8_0 transformer (7.6 GB) plus the base text encoder and VAE"),
            ("Qwen/Qwen-Image-2.1-Turbo", 32.5, "the same checkpoint at full precision"),
            ("stable-diffusion-v1-5/stable-diffusion-v1-5", 2.1, "Stable Diffusion 1.5, fp16 — the smallest and fastest; 512 px native, modest quality"),
            ("ByteDance/SDXL-Lightning", 6.9, "SDXL-Lightning 4-step, fp16 — fast, good quality at 1024 px; also downloads SDXL base's text encoders and VAE"),
        ],
    },
    "video": {
        "runner": "huggingface", "engine": "diffusers", "family": ("wan", "ltx"), "files": None,
        "deps": ("torch", "diffusers", "ftfy"), "platform": None,
        "search": {"filter": ["gguf"], "pipeline_tag": "text-to-video"},
        "kind": "GGUF · Wan / LTX-Video",
        "accepts": "A Wan 2.2 or LTX-Video GGUF repo id or Hugging Face page URL, e.g. QuantStack/Wan2.2-TI2V-5B-GGUF. Downloads to the HF cache.",
        "example": "QuantStack/Wan2.2-TI2V-5B-GGUF",
        "recommended": [
            ("QuantStack/Wan2.2-TI2V-5B-GGUF", 19.6, "shipped default — Wan 2.2 5B, Q8_0; text and image to video, with its text encoder and VAE"),
            ("city96/LTX-Video-0.9.6-distilled-gguf", 22.8, "LTX-Video 2B distilled, Q8_0; fast, 8 steps, with its text encoder and VAE (untested here)"),
        ],
    },
}

ROLES = config.ROLES
OLLAMA_ROLES = tuple(role for role in ROLES if ROLE_SPECS[role]["runner"] == "ollama")
# the roles omni runs in its own uvicorn worker, one model each
PYTHON_WORKERS = tuple(role for role in ROLES if role not in OLLAMA_ROLES)


def unavailable_reason(role: str) -> str | None:
    """Why this worker cannot run on this machine, or None when it can."""
    spec = ROLE_SPECS.get(role)
    if spec is None:
        return None
    missing = [dep for dep in spec["deps"] if importlib.util.find_spec(dep) is None]
    if missing:
        return (f"{role} cannot run: this install has no {', '.join(missing)}, which come with the media extra. "
                'Reinstall with it: uv tool install --force "omni-api[media]" (or brew reinstall omni if you installed with Homebrew)')
    if spec["platform"] and sys.platform != spec["platform"]:
        return f"{role} renders on Metal (MPS), which this {sys.platform} machine does not have"
    return None


# the voices each text-to-speech family ships, and the one a family uses when the configured voice is not its own
TTS_VOICES = {
    "kokoro": schemas.TTS_VOICES,
    "kitten": ("Bella", "Jasper", "Luna", "Bruno", "Rosie", "Hugo", "Kiki", "Leo"),
    "supertonic": ("M1", "M2", "M3", "M4", "M5", "F1", "F2", "F3", "F4", "F5"),
}
TTS_FALLBACK_VOICE = {"kokoro": "af_heart", "kitten": "Jasper", "supertonic": "M1"}


def tts_voices(model: str) -> tuple[str, ...]:
    """The voices a text-to-speech model offers; empty for a repo no engine here loads."""
    return TTS_VOICES.get(backend("tts", model) or "", ())


def tts_default_voice(model: str, configured: str) -> str:
    """The configured voice when this model has it, else the model's own default."""
    return configured if configured in tts_voices(model) else TTS_FALLBACK_VOICE.get(backend("tts", model) or "", configured)


def reads_images(role: str, name: str) -> bool:
    """Whether the role's engine for this model accepts an image; the Laya and GLiNER decide engines read text only."""
    return not (role == "decide" and backend(role, name) in ("laya", "gliner2"))


def require_backend_deps(role: str, name: str) -> None:
    """Refuse a model whose engine package this install lacks, saying how to get it."""
    needed = ROLE_SPECS[role].get("backend_deps", {}).get(backend(role, name) or "", ())
    missing = [dep for dep in needed if importlib.util.find_spec(dep) is None]
    if missing:
        raise config.ConfigError(
            f"{role}.model {name!r} needs {', '.join(missing)}, which come with the media extra. "
            'Reinstall with it: uv tool install --force "omni-api[media]" (or brew reinstall omni if you installed with Homebrew)')


def backend(role: str, name: str) -> str | None:
    """Which of the role's model families `name` belongs to (`kitten` for a Kitten TTS repo), or None."""
    lowered = name.lower()
    for marker in family(role):
        if re.search(rf"(?<![a-z0-9]){re.escape(marker)}", lowered):
            return marker
    return None


def files(role: str, name: str | None = None) -> list[str] | None:
    """The files a fixed-layout runtime reads from its repo, or None when it reads the whole repo.

    A role with several model families keeps one list per family, so `name` picks the list.
    """
    patterns = ROLE_SPECS[role]["files"]
    if isinstance(patterns, dict):
        return patterns.get(backend(role, name or ""))
    return patterns


def canonical(name) -> str:
    return name.removesuffix(":latest") if isinstance(name, str) else ""


def matches(name, model) -> bool:
    return bool(name) and bool(model) and canonical(name) == canonical(model)


def runner(role: str) -> str:
    return ROLE_SPECS[role]["runner"]


def family(role: str) -> tuple[str, ...]:
    """The repo-name markers a role's runtime accepts, e.g. `qwen-image-2` for image; empty when any repo will do."""
    return ROLE_SPECS[role]["family"]


def has_family(role: str, name: str) -> bool:
    """Whether a repo name carries one of the role's markers at a word start: `Wan2.2` is Wan, `swan` is not."""
    markers = family(role)
    lowered = name.lower()
    return not markers or any(re.search(rf"(?<![a-z0-9]){re.escape(marker)}", lowered) for marker in markers)


def normalize_repo_id(name: str) -> str:
    """Turn a pasted model page URL into the id the runtimes take.

    Accepts `https://huggingface.co/user/repo`, `huggingface.co/user/repo`,
    `hf.co/user/repo` and bare `user/repo`, and drops page suffixes such as
    `/tree/main` or `?private=true`. The `hf.co/` prefix and any `:tag` stay:
    Ollama pulls `hf.co/user/repo-GGUF:Q8_0` verbatim.
    """
    text = name.strip().strip("/")
    text = re.sub(r"^https?://(www\.)?", "", text)
    text = text.removeprefix("huggingface.co/")
    parts = text.split("/")
    markers = ("tree", "blob", "resolve", "commit", "discussions", "pulls")
    # bare user/repo/tree/... and hf.co/user/repo/tree/... both drop the page suffix
    if len(parts) > 3 and parts[2] in markers:
        text = "/".join(parts[:2])
    elif len(parts) > 4 and parts[0] == "hf.co" and parts[3] in markers:
        text = "/".join(parts[:3])
    return text.split("?")[0].split("#")[0].rstrip("/")


def bare_repo_id(name: str) -> str:
    """The Hugging Face repo id: normalized, without the `hf.co/` prefix or `:tag` a pull used."""
    text = normalize_repo_id(name)
    text = text.removeprefix("hf.co/")
    return text.split(":")[0]


def repo_name(role: str, model: str) -> str:
    """The id a role's runtime takes for a pasted model name: Ollama keeps `hf.co/` and `:tag`, the rest do not."""
    name = normalize_repo_id(model)
    return name if runner(role) == "ollama" else bare_repo_id(name)


def supports(role: str, name: str) -> bool:
    """Whether this role's runtime can load a repo by name (used to filter cached repos).

    The marker is the role's own search family, so cached repos are held to the same standard the
    search endpoint applies: `Qwen/Qwen-Image-2.1` is offered to image, `someone/Qwen-Image-XL` is not.
    """
    if "mlx" in name.lower():  # Apple's MLX weights: no worker here reads them
        return False
    return has_family(role, name)


def require(role: str, name: str) -> None:
    """Refuse a pinned model this role's runtime cannot load, before anything is downloaded."""
    if not supports(role, bare_repo_id(name)):
        raise config.ConfigError(
            f"{role}.model {name!r} is not a {', '.join(family(role))} repo, which is what this role's runtime loads")
