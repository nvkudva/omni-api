"""Roles, which runtime serves each, and what each runtime can actually load."""
import importlib.util
import re
import sys

from omni import config

# chat, decide and embed share the machine's Ollama; the rest run in their own uvicorn worker.
ROLES = config.ROLES
OLLAMA_ROLES = ("chat", "decide", "embed")
HF_ROLES = ("stt", "tts", "image", "video")
PYTHON_WORKERS = HF_ROLES

# What each Python worker must be able to import to run at all. MLX ships macOS, Linux
# (CUDA or CPU) and Windows wheels, so only the image worker is platform-bound: it renders
# on Metal through torch's MPS backend.
MEDIA_DEPS = {
    "stt": ("mlx", "parakeet_mlx"),
    "tts": ("mlx", "mlx_audio"),
    "video": ("mlx", "ltx_pipelines_mlx"),
    "image": ("torch", "diffusers"),
}
MPS_ROLES = ("image",)


def unavailable_reason(role: str) -> str | None:
    """Why this worker cannot run on this machine, or None when it can."""
    deps = MEDIA_DEPS.get(role)
    if deps is None:
        return None
    missing = [dep for dep in deps if importlib.util.find_spec(dep) is None]
    if missing:
        return f"{role} is not installed here: {', '.join(missing)} missing (install the media extra)"
    if role in MPS_ROLES and sys.platform != "darwin":
        return f"{role} renders on Metal (MPS), which this {sys.platform} machine does not have"
    return None

# A repo id must contain the role's family marker to be loadable by that role's runtime; the marker
# lives in SEARCH_FILTERS below, so search and the cached-repo filter agree by construction.

# What a role's runtime can load, for the Hugging Face search endpoint.
# Ollama takes GGUF; the workers use parakeet-mlx, mlx-audio, diffusers' QwenImage21Pipeline and ltx-pipelines-mlx.
SEARCH_FILTERS = {
    "chat": {"filter": ["gguf"], "pipeline_tag": "text-generation"},
    "decide": {"filter": ["gguf"], "pipeline_tag": "text-generation"},
    "embed": {"filter": ["gguf"], "pipeline_tag": "feature-extraction"},
    "stt": {"filter": ["mlx"], "pipeline_tag": "automatic-speech-recognition", "family": "parakeet"},
    "tts": {"filter": ["mlx"], "pipeline_tag": "text-to-speech", "family": "kokoro"},
    "image": {"filter": ["diffusers"], "pipeline_tag": "text-to-image", "family": "qwen-image-2"},
    "video": {"filter": ["mlx"], "family": "ltx"},
}


def canonical(name) -> str:
    return name.removesuffix(":latest") if isinstance(name, str) else ""


def matches(name, model) -> bool:
    return bool(name) and bool(model) and canonical(name) == canonical(model)


def runner(role: str) -> str:
    return "ollama" if role in OLLAMA_ROLES else "huggingface"


def family(role: str) -> str | None:
    """The repo-name marker a role's runtime needs, e.g. `qwen-image-2` for image."""
    return SEARCH_FILTERS.get(role, {}).get("family")


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


def supports(role: str, name: str) -> bool:
    """Whether this role's runtime can load a repo by name (used to filter cached repos).

    The marker is the role's own search family, so cached repos are held to the same standard the
    search endpoint applies: `Qwen/Qwen-Image-2.1` is offered to image, `someone/Qwen-Image-XL` is not.
    """
    marker = family(role)
    return marker is None or marker in name.lower()
