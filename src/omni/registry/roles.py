"""Roles, which runtime serves each, and what each runtime can actually load."""
from omni import config

# chat, decide and embed share the machine's Ollama; the rest run in their own uvicorn worker.
ROLES = config.ROLES
OLLAMA_ROLES = ("chat", "decide", "embed")
HF_ROLES = ("stt", "tts", "image", "video")
PYTHON_WORKERS = HF_ROLES

# A repo id must contain the family name to be loadable by that role's runtime.
FAMILIES = {
    "stt": "parakeet",
    "tts": "kokoro",
    "image": "qwen-image",
    "video": "ltx",
}

# What a role's runtime can load, for the Hugging Face search endpoint.
# Ollama takes GGUF; the workers use parakeet-mlx, mlx-audio, diffusers' QwenImage21Pipeline and ltx-pipelines-mlx.
SEARCH_FILTERS = {
    "chat": {"filter": ["gguf"]},
    "decide": {"filter": ["gguf"], "pipeline_tag": "text-generation"},
    "embed": {"filter": ["gguf"], "pipeline_tag": "feature-extraction"},
    "stt": {"filter": ["mlx"], "pipeline_tag": "automatic-speech-recognition", "family": "parakeet"},
    "tts": {"filter": ["mlx"], "pipeline_tag": "text-to-speech", "family": "kokoro"},
    "image": {"filter": ["diffusers"], "pipeline_tag": "text-to-image", "family": "qwen-image-2"},
    "video": {"filter": ["mlx"], "family": "ltx"},
}


def runner(role: str) -> str:
    return "ollama" if role in OLLAMA_ROLES else "huggingface"


def supports(role: str, name: str) -> bool:
    """Whether this role's runtime can load a repo by name (used to filter cached repos)."""
    family = FAMILIES.get(role)
    return family is None or family in name.lower()
