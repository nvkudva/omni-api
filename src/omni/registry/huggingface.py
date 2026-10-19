"""The Hugging Face cache: what is downloaded, how big it is, search, and delete."""
import os
import time
from pathlib import Path

from huggingface_hub import HfApi, constants, scan_cache_dir, snapshot_download

from omni.registry.roles import SEARCH_FILTERS, runner

CACHE_TTL_SECONDS = 30.0
_installed_cache: tuple[float, list[dict]] | None = None


def installed(force: bool = False) -> list[dict]:
    """Model repos in the HF cache. Re-walking the whole cache is expensive, so the result is cached briefly."""
    global _installed_cache
    now = time.monotonic()
    if not force and _installed_cache is not None and now - _installed_cache[0] < CACHE_TTL_SECONDS:
        return _installed_cache[1]
    cache = scan_cache_dir()
    models = [{"name": repo.repo_id, "size_bytes": repo.size_on_disk}
              for repo in cache.repos if repo.repo_type == "model"]
    _installed_cache = (now, models)
    return models


def invalidate() -> None:
    """Forget the cache after a pull or a delete."""
    global _installed_cache
    _installed_cache = None


def size(repo_id: str) -> int:
    info = HfApi().model_info(repo_id, files_metadata=True)
    return sum(sibling.size or 0 for sibling in info.siblings or [])


def cache_dir(repo_id: str) -> Path:
    return Path(constants.HF_HUB_CACHE) / f"models--{repo_id.replace('/', '--')}"


def folder_size(folder: Path) -> int:
    """Bytes downloaded so far. HF keeps the real blobs under `blobs/`, so walk only that when it exists."""
    blobs = folder / "blobs"
    root = blobs if blobs.is_dir() else folder
    if not root.exists():
        return 0
    total = 0
    for entry in root.rglob("*"):
        try:
            if entry.is_file() and not entry.is_symlink():
                total += entry.stat().st_size
        except OSError:
            continue
    return total


def _dtype_bytes(dtype: str) -> float:
    """Bytes per parameter from a safetensors dtype name like `BF16` or `F8_E4M3`."""
    name = dtype.upper()
    if "64" in name:
        return 8
    if "32" in name:
        return 4
    if "16" in name:
        return 2
    if "8" in name:
        return 1
    if "4" in name:
        return 0.5
    if "BOOL" in name:
        return 0.125
    return 2


def approx_size_bytes(repo) -> int | None:
    """Approximate download size without extra Hub calls: exact GGUF file bytes when reported,
    else safetensors parameter counts times their dtype width."""
    gguf = getattr(repo, "gguf", None) or {}
    file_size = gguf.get("totalFileSize") if isinstance(gguf, dict) else getattr(gguf, "totalFileSize", None)
    if file_size:
        return int(file_size)
    safetensors = getattr(repo, "safetensors", None) or {}
    parameters = safetensors.get("parameters") if isinstance(safetensors, dict) else getattr(safetensors, "parameters", None)
    if not parameters:
        return None
    return int(sum(count * _dtype_bytes(dtype) for dtype, count in parameters.items()))


def search(role: str, query: str, limit: int = 20) -> list[dict]:
    """Hugging Face repos this role's runtime can load, most downloaded first."""
    filters = dict(SEARCH_FILTERS[role])
    family = filters.pop("family", None)
    terms = query.strip() or family
    expand = ["downloads", "likes", "pipeline_tag", "safetensors"]
    if runner(role) == "ollama":
        expand.append("gguf")
    found = HfApi().list_models(search=terms, sort="downloads", limit=limit * 3 if family else limit,
                                expand=expand, **filters)
    results = []
    for repo in found:
        if family and family not in repo.id.lower():
            continue
        name = f"hf.co/{repo.id}" if runner(role) == "ollama" else repo.id
        results.append({"name": name, "id": repo.id, "downloads": repo.downloads or 0, "likes": repo.likes or 0,
                        "pipeline_tag": repo.pipeline_tag, "size_bytes": approx_size_bytes(repo)})
    return results[:limit]


def delete(repo_id: str) -> None:
    """Remove every cached revision of a repo."""
    cache = scan_cache_dir()
    revisions = [revision.commit_hash for repo in cache.repos if repo.repo_id == repo_id
                 for revision in repo.revisions]
    if not revisions:
        raise FileNotFoundError(f"{repo_id} is not in the Hugging Face cache")
    cache.delete_revisions(*revisions).execute()
    invalidate()


def download(repo_id: str) -> str:
    """Blocking download; called in a thread. Returns the snapshot path."""
    return snapshot_download(repo_id)


def disk_usage_root() -> Path:
    cache = Path(constants.HF_HUB_CACHE)
    return cache if os.path.isdir(cache) else Path.home()
