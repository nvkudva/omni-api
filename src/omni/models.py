"""What each role runs, what is installed, and whether the machine has the memory for it."""
import asyncio
import json
import os
import shutil
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import psutil
from huggingface_hub import HfApi, constants, scan_cache_dir, snapshot_download
from huggingface_hub.errors import HfHubHTTPError

OLLAMA_ROLES = ("chat", "decide", "embed")
HF_ROLES = ("stt", "tts", "image", "video")
GB = 1024**3
LOW_MEMORY_FRACTION = 0.15
CPU_OVERLOAD_PERCENT = 90
PROGRESS_SECONDS = 2.0
LOAD_KEEP_ALIVE = "10m"
# What each role's runtime can actually load: Ollama takes GGUF; the workers use parakeet-mlx, mlx-audio,
# diffusers' QwenImage21Pipeline and ltx-pipelines-mlx, so ids must also contain the family name.
SEARCH_FILTERS = {
    "chat": {"filter": ["gguf"]},
    "decide": {"filter": ["gguf"], "pipeline_tag": "text-generation"},
    "embed": {"filter": ["gguf"], "pipeline_tag": "feature-extraction"},
    "stt": {"filter": ["mlx"], "pipeline_tag": "automatic-speech-recognition", "family": "parakeet"},
    "tts": {"filter": ["mlx"], "pipeline_tag": "text-to-speech"},
    "image": {"filter": ["diffusers"], "pipeline_tag": "text-to-image", "family": "qwen-image-2"},
    "video": {"filter": ["mlx"], "family": "ltx"},
}


def runner(role: str) -> str:
    return "ollama" if role in OLLAMA_ROLES else "huggingface"


def _gb(size: int) -> str:
    return f"{size / GB:.1f} GB"


async def ollama_installed(client: httpx.AsyncClient) -> list[dict]:
    response = await client.get("/api/tags", timeout=5.0)
    response.raise_for_status()
    installed = []
    for model in response.json().get("models", []):
        details = model.get("details", {})
        installed.append({
            "name": model["name"],
            "size_bytes": model["size"],
            "parameters": details.get("parameter_size"),
            "quantization": details.get("quantization_level"),
        })
    return installed


async def capabilities(client: httpx.AsyncClient, name: str) -> list[str]:
    """Ollama's own feature list for a model, e.g. ["completion", "vision"]."""
    try:
        response = await client.post("/api/show", json={"model": name}, timeout=5.0)
        response.raise_for_status()
        return response.json().get("capabilities", [])
    except (httpx.HTTPError, ValueError):
        return []


def hf_installed() -> list[dict]:
    cache = scan_cache_dir()
    repos = [repo for repo in cache.repos if repo.repo_type == "model"]
    return [{"name": repo.repo_id, "size_bytes": repo.size_on_disk} for repo in repos]


def _hf_size(repo_id: str) -> int:
    info = HfApi().model_info(repo_id, files_metadata=True)
    return sum(sibling.size or 0 for sibling in info.siblings or [])


def _hf_cache_dir(repo_id: str) -> Path:
    return Path(constants.HF_HUB_CACHE) / f"models--{repo_id.replace('/', '--')}"


def _folder_size(folder: Path) -> int:
    total = 0
    for root, _, files in os.walk(folder):
        for name in files:
            path = Path(root, name)
            if not path.is_symlink():
                total += path.stat().st_size
    return total


def search(role: str, query: str, limit: int = 20) -> list[dict]:
    """Hugging Face repos this role's runtime can load, most downloaded first."""
    filters = dict(SEARCH_FILTERS[role])
    family = filters.pop("family", None)
    terms = query.strip() or family
    found = HfApi().list_models(search=terms, sort="downloads", limit=limit * 3 if family else limit,
                                expand=["downloads", "likes", "pipeline_tag"], **filters)
    results = []
    for repo in found:
        if family and family not in repo.id.lower():
            continue
        name = f"hf.co/{repo.id}" if runner(role) == "ollama" else repo.id
        results.append({"name": name, "id": repo.id, "downloads": repo.downloads or 0, "likes": repo.likes or 0,
                        "pipeline_tag": repo.pipeline_tag})
    return results[:limit]


async def ollama_load(client: httpx.AsyncClient, role: str, model: str, keep_alive: str | int = LOAD_KEEP_ALIVE) -> None:
    """Load `model` into Ollama's memory, or with keep_alive=0 unload it."""
    if role == "embed":
        request = ("/api/embed", {"model": model, "input": "", "keep_alive": keep_alive})
    else:
        request = ("/api/generate", {"model": model, "keep_alive": keep_alive})
    response = await client.post(request[0], json=request[1], timeout=None)
    response.raise_for_status()


async def ollama_unload(client: httpx.AsyncClient, role: str, model: str) -> None:
    await ollama_load(client, role, model, keep_alive=0)


def overload_warnings(cpu: float | None = None) -> list[str]:
    """Why loading another model right now would push the machine over."""
    warnings = []
    ram = psutil.virtual_memory()
    swap = psutil.swap_memory()
    if ram.available < ram.total * LOW_MEMORY_FRACTION:
        warnings.append(f"memory overload: {_gb(ram.available)} free of {_gb(ram.total)}")
    if swap.used > GB:
        warnings.append(f"{_gb(swap.used)} swapped out: models will run slowly")
    if cpu is None:
        cpu = psutil.cpu_percent(interval=None)
    if cpu > CPU_OVERLOAD_PERCENT:
        warnings.append(f"CPU overload: {cpu:.0f}% busy")
    load_1m = os.getloadavg()[0]
    cores = os.cpu_count() or 1
    if load_1m > cores:
        warnings.append(f"load average {load_1m:.1f} exceeds {cores} cores: work is queuing")
    return warnings


def hf_delete(repo_id: str) -> None:
    cache = scan_cache_dir()
    revisions = [revision.commit_hash for repo in cache.repos if repo.repo_id == repo_id
                 for revision in repo.revisions]
    cache.delete_revisions(*revisions).execute()


def fit_warnings(size_bytes: int) -> list[str]:
    """Why a model of this many bytes of weights may not fit on this machine right now."""
    warnings = []
    ram = psutil.virtual_memory()
    disk_free = shutil.disk_usage(constants.HF_HUB_CACHE if Path(constants.HF_HUB_CACHE).exists() else Path.home()).free
    if size_bytes > disk_free:
        warnings.append(f"needs {_gb(size_bytes)} of disk; only {_gb(disk_free)} free")
    if size_bytes > ram.total * 0.75:
        warnings.append(f"{_gb(size_bytes)} of weights on a {_gb(ram.total)} machine: expect heavy swapping, "
                        "or a crash if it loads all at once")
    elif size_bytes > ram.available:
        warnings.append(f"{_gb(size_bytes)} of weights but only {_gb(ram.available)} RAM free now: "
                        "close apps or let other models unload first")
    return warnings


async def memory(client: httpx.AsyncClient, workers: dict[str, dict]) -> dict:
    ram = psutil.virtual_memory()
    swap = psutil.swap_memory()
    try:
        response = await client.get("/api/ps", timeout=2.0)
        loaded = response.json().get("models", [])
    except (httpx.HTTPError, ValueError):
        loaded = []
    ollama = [{"name": model["name"], "size_bytes": model.get("size", 0), "vram_bytes": model.get("size_vram", 0)}
              for model in loaded]
    cpu = psutil.cpu_percent(interval=None)
    return {
        "total_bytes": ram.total,
        "available_bytes": ram.available,
        "swap_used_bytes": swap.used,
        "disk_free_bytes": shutil.disk_usage(Path.home()).free,
        "ollama": ollama,
        "cpu_percent": cpu,
        "cpu_count": os.cpu_count(),
        "load_average": os.getloadavg(),
        "workers": workers,
        "warnings": overload_warnings(cpu),
    }


async def pull(client: httpx.AsyncClient, role: str, model: str) -> AsyncIterator[dict]:
    """Progress events while `model` downloads, ending in {"status": "success"} or {"error": ...}."""
    events = _ollama_pull(client, model) if runner(role) == "ollama" else _hf_pull(model)
    async for event in events:
        yield event


async def _ollama_pull(client: httpx.AsyncClient, model: str) -> AsyncIterator[dict]:
    warned = False
    try:
        async with client.stream("POST", "/api/pull", json={"model": model}) as response:
            async for line in response.aiter_lines():
                if not line:
                    continue
                event = json.loads(line)
                # the biggest layer is the weights; its size is the first sign of whether they fit
                if not warned and event.get("total", 0) > 100 * 1024**2:
                    warned = True
                    for warning in fit_warnings(event["total"]):
                        yield {"warning": warning}
                yield event
    except httpx.HTTPError as error:
        yield {"error": f"ollama: {error}"}


async def _hf_pull(repo_id: str) -> AsyncIterator[dict]:
    try:
        total = await asyncio.to_thread(_hf_size, repo_id)
    except HfHubHTTPError as error:
        yield {"error": f"huggingface: {error}"}
        return
    for warning in fit_warnings(total):
        yield {"warning": warning}
    download = asyncio.create_task(asyncio.to_thread(snapshot_download, repo_id))
    folder = _hf_cache_dir(repo_id)
    while not download.done():
        completed = await asyncio.to_thread(_folder_size, folder)
        yield {"status": f"downloading {repo_id}", "total": total, "completed": completed}
        await asyncio.wait({download}, timeout=PROGRESS_SECONDS)
    try:
        download.result()
    except (HfHubHTTPError, OSError) as error:
        yield {"error": f"huggingface: {error}"}
        return
    yield {"status": "success"}
