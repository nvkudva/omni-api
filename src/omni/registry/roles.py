"""Roles, which runtime serves each, and what each runtime can actually load."""
import importlib.util
import re
import sys

from omni import config

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
            ("qwen2.5:3b", 1.9, "next step up — noticeably better instructions"),
            ("qwen3.8:27b", 17.7, "best all-round quality"),
            ("gemma4:26b", 18.7, "Mixture-of-Experts, fast for its size"),
            ("gpt-oss:20b", 13.8, "strong reasoning"),
            ("qwen3-coder:30b", 18.6, "code and tool calls"),
            ("gemma4:12b", 8.0, "smallest of the fast ones"),
        ],
    },
    "decide": {
        "runner": "huggingface", "engine": "onnxruntime", "family": ("vega",), "platform": None,
        "files": ["v2/embed_q8.onnx*", "v2/decoder_q8.onnx*", "v2/engine.onnx", "v2/tokenizer.json",
                  "v2/vega_web.json", "v2/vision_q8.onnx*"],
        "deps": ("onnxruntime", "tokenizers"),
        "search": {"filter": ["onnx"], "pipeline_tag": "text-classification"},
        "kind": "ONNX · Vega typed decisions",
        "accepts": "A Vega ONNX repo id with the v2 layout or Hugging Face page URL, e.g. nvkudva/vega-onnx-q8. Downloads to the HF cache.",
        "example": "nvkudva/vega-onnx-q8",
        "recommended": [
            ("nvkudva/vega-onnx-q8", 0.93, "shipped default — Vega 0.8B, 8-bit ONNX; calibrated, with prediction sets and abstain"),
        ],
    },
    "embed": {
        "runner": "ollama", "engine": "ollama", "family": (), "files": None, "deps": (), "platform": None,
        "search": {"filter": ["gguf"], "pipeline_tag": "feature-extraction"},
        "kind": "GGUF · feature extraction",
        "accepts": "Ollama: a library name (embeddinggemma-2) or any GGUF repo on Hugging Face as hf.co/user/repo-GGUF:Q8_0.",
        "example": "hf.co/user/repo-GGUF:Q8_0",
        "recommended": [
            ("embeddinggemma-2:270m", 0.3, "shipped default — smallest"),
            ("embeddinggemma-2", 1.3, "multimodal, long context"),
            ("qwen3-embedding:0.6b", 0.6, "small, good quality"),
            ("bge-m3", 1.2, "multilingual"),
            ("qwen3-embedding:8b", 4.7, "best quality; 4096-dim vectors"),
        ],
    },
    "stt": {
        "runner": "huggingface", "engine": "onnx-asr", "family": ("parakeet",), "platform": None,
        "files": ["config.json", "vocab.txt", "encoder-model.int8.onnx", "decoder_joint-model.int8.onnx"],
        "deps": ("onnxruntime", "onnx_asr"),
        "search": {"filter": ["onnx"], "pipeline_tag": "automatic-speech-recognition"},
        "kind": "ONNX · Parakeet speech-to-text",
        "accepts": "ONNX Parakeet repo id (with int8 files) or Hugging Face page URL, e.g. istupakov/parakeet-tdt-0.6b-v2-onnx. Downloads to the HF cache.",
        "example": "istupakov/parakeet-tdt-0.6b-v2-onnx",
        "recommended": [
            ("istupakov/parakeet-tdt-0.6b-v2-onnx", 0.7, "shipped default — English, int8"),
            ("istupakov/parakeet-tdt-0.6b-v3-onnx", 0.7, "25 European languages, int8"),
        ],
    },
    "tts": {
        "runner": "huggingface", "engine": "onnxruntime", "family": ("kokoro",), "platform": None,
        "files": ["config.json", "tokenizer.json", "onnx/model_quantized.onnx", "voices/*.bin"],
        "deps": ("onnxruntime", "misaki"),
        "search": {"filter": ["onnx"], "pipeline_tag": "text-to-speech"},
        "kind": "ONNX · Kokoro text-to-speech",
        "accepts": "Kokoro ONNX repo id or Hugging Face page URL, e.g. onnx-community/Kokoro-82M-v1.0-ONNX. Downloads to the HF cache.",
        "example": "onnx-community/Kokoro-82M-v1.0-ONNX",
        "recommended": [
            ("onnx-community/Kokoro-82M-v1.0-ONNX", 0.12, "shipped default — int8, 27 voices"),
        ],
    },
    "image": {
        "runner": "huggingface", "engine": "diffusers", "family": ("qwen-image-2",), "files": None,
        "deps": ("torch", "diffusers"), "platform": "darwin",
        "search": {"filter": ["diffusers"], "pipeline_tag": "text-to-image"},
        "kind": "Diffusers · Qwen-Image-2",
        "accepts": "Qwen-Image-2 repo id or Hugging Face page URL, e.g. Qwen/Qwen-Image-2.1. Downloads to the HF cache.",
        "example": "Qwen/Qwen-Image-2.1",
        "recommended": [
            ("unsloth/Qwen-Image-2.1-Turbo-GGUF", 25.9, "shipped default — Q8_0 transformer (7.6 GB) plus the base text encoder and VAE"),
            ("Qwen/Qwen-Image-2.1-Turbo", 32.5, "the same checkpoint at full precision"),
            ("Qwen/Qwen-Image-2.1", 33.1, "full quality, more steps"),
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


def files(role: str) -> list[str] | None:
    """The files a fixed-layout runtime reads from its repo, or None when it reads the whole repo."""
    return ROLE_SPECS[role]["files"]


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
