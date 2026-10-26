"""The Hugging Face cache: what is downloaded, how big it is, search, and delete."""
import asyncio
import contextlib
import fnmatch
import json
import os
import signal
import sys
import time
from pathlib import Path
from typing import NamedTuple

from huggingface_hub import HfApi, constants, scan_cache_dir
from huggingface_hub.errors import CacheNotFound, HfHubHTTPError

from omni import schemas
from omni.registry.roles import SEARCH_FILTERS, family, runner
from omni.supervisor import guard

# what a Hub call can raise that a route should report as a bad gateway, not a crash
ERRORS = (HfHubHTTPError, OSError)

SPEECH_FILES = {"decide": schemas.DECIDE_FILES, "stt": schemas.STT_FILES, "tts": schemas.TTS_FILES}
CACHE_TTL_SECONDS = 30.0
_installed_cache: tuple[float, list[dict]] | None = None


def installed(force: bool = False) -> list[dict]:
    """Model repos in the HF cache. Re-walking the whole cache is expensive, so the result is cached briefly."""
    global _installed_cache
    now = time.monotonic()
    if not force and _installed_cache is not None and now - _installed_cache[0] < CACHE_TTL_SECONDS:
        return _installed_cache[1]
    try:
        cache = scan_cache_dir()
    except CacheNotFound:  # nothing has been downloaded on this machine yet
        return []
    models = [{"name": repo.repo_id, "size_bytes": repo.size_on_disk}
              for repo in cache.repos if repo.repo_type == "model"]
    _installed_cache = (now, models)
    return models


def invalidate() -> None:
    """Forget the cache after a pull or a delete."""
    global _installed_cache
    _installed_cache = None


class Plan(NamedTuple):
    """What a role's download covers: the files to fetch, or None for the whole repo."""
    files: list[str] | None
    size: int


def preferred_gguf(names: list[str]) -> str | None:
    """The GGUF an 8-bit load takes: Q8_0 when the repo ships one, else the first file."""
    files = [name for name in names if name.endswith(".gguf")]
    eight_bit = [name for name in files if "Q8_0" in name]
    return (eight_bit or files or [None])[0]


def download_plan(role: str, repo_id: str) -> Plan:
    """The files this role loads, so a quant repo does not download every quant.

    `unsloth/Qwen-Image-2.1-Turbo-GGUF` holds twelve GGUFs (65 GB); the image worker loads one,
    and pulling the rest costs 57 GB nothing reads. The speech repos ship fp32, fp16 and int8
    ONNX side by side; the workers read the int8 ones.
    """
    siblings = HfApi().model_info(repo_id, files_metadata=True).siblings or []
    if role in SPEECH_FILES:
        wanted = [sibling for sibling in siblings if any(fnmatch.fnmatch(sibling.rfilename, pattern) for pattern in SPEECH_FILES[role])]
        if wanted:
            return Plan(SPEECH_FILES[role], sum(sibling.size or 0 for sibling in wanted))
    if role in ("image", "video"):
        file = preferred_gguf([sibling.rfilename for sibling in siblings])
        if file is not None:
            return Plan([file], next((sibling.size or 0) for sibling in siblings if sibling.rfilename == file))
    return Plan(None, sum(sibling.size or 0 for sibling in siblings))


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
    filters.pop("family", None)
    markers = family(role)
    terms = query.strip() or (markers[0] if markers else None)
    expand = ["downloads", "likes", "pipeline_tag", "safetensors"]
    if runner(role) == "ollama":
        expand.append("gguf")
    found = HfApi().list_models(search=terms, sort="downloads", limit=limit * 3 if markers else limit,
                                expand=expand, **filters)
    results = []
    for repo in found:
        if markers and not any(marker in repo.id.lower() for marker in markers):
            continue
        name = f"hf.co/{repo.id}" if runner(role) == "ollama" else repo.id
        results.append({"name": name, "id": repo.id, "downloads": repo.downloads or 0, "likes": repo.likes or 0,
                        "pipeline_tag": repo.pipeline_tag, "size_bytes": approx_size_bytes(repo)})
    return results[:limit]


def delete(repo_id: str) -> None:
    """Remove every cached revision of a repo."""
    try:
        cache = scan_cache_dir()
    except CacheNotFound as error:
        raise FileNotFoundError(f"{repo_id} is not in the Hugging Face cache") from error
    revisions = [revision.commit_hash for repo in cache.repos if repo.repo_id == repo_id
                 for revision in repo.revisions]
    if not revisions:
        raise FileNotFoundError(f"{repo_id} is not in the Hugging Face cache")
    cache.delete_revisions(*revisions).execute()
    invalidate()


DOWNLOAD_SCRIPT = (
    "import json, sys\n"
    "from huggingface_hub import snapshot_download\n"
    "from huggingface_hub.errors import GatedRepoError\n"
    "files = json.loads(sys.argv[2]) if len(sys.argv) > 2 else None\n"
    "try:\n"
    "    snapshot_download(sys.argv[1], allow_patterns=files)\n"
    "except Exception as error:\n"
    "    print(json.dumps({'error': str(error), 'gated': isinstance(error, GatedRepoError)}), file=sys.stderr)\n"
    "    sys.exit(1)\n"
)
TERMINATE_GRACE_SECONDS = 5.0
STDERR_TAIL_CHARS = 2000


def download_command(repo_id: str, files: list[str] | None = None) -> list[str]:
    command = [sys.executable, "-c", DOWNLOAD_SCRIPT, repo_id]
    return [*command, json.dumps(files)] if files else command


def failure_message(stderr: bytes, code: int | None) -> str:
    """The error a failed download child reported, with 'gated repo' kept in the text so is_gated still matches."""
    lines = stderr.decode(errors="replace").strip().splitlines()
    if lines and lines[-1].startswith("{"):
        try:
            report = json.loads(lines[-1])
        except ValueError:
            report = None
        if isinstance(report, dict) and report.get("error"):
            return f"gated repo: {report['error']}" if report.get("gated") else str(report["error"])
    tail = "\n".join(lines)[-STDERR_TAIL_CHARS:]
    return tail or f"download exited with status {code}"


async def stop_process(process: asyncio.subprocess.Process) -> None:
    """Terminate the child's process group, escalating to SIGKILL, and reap it."""
    if process.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
        try:
            await asyncio.wait_for(process.wait(), TERMINATE_GRACE_SECONDS)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
    await process.wait()


async def download(repo_id: str, files: list[str] | None = None) -> None:
    """Download in a child process so cancelling really stops it. Raises OSError on failure."""
    process = await asyncio.create_subprocess_exec(
        *guard.command(download_command(repo_id, files)), stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE, start_new_session=True,
        env={**os.environ, "HF_HUB_DISABLE_PROGRESS_BARS": "1"})
    try:
        _, stderr = await process.communicate()
    finally:
        await asyncio.shield(stop_process(process))
    if process.returncode != 0:
        raise OSError(failure_message(stderr, process.returncode))


def disk_usage_root() -> Path:
    cache = Path(constants.HF_HUB_CACHE)
    return cache if os.path.isdir(cache) else Path.home()
