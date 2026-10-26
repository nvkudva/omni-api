"""The Hugging Face cache: what is downloaded, how big it is, search, and delete."""
import asyncio
import contextlib
import fnmatch
import json
import os
import shutil
import signal
import sys
import time
from pathlib import Path
from typing import NamedTuple

from huggingface_hub import HfApi, constants, scan_cache_dir
from huggingface_hub.errors import CacheNotFound, HfHubHTTPError

from omni import config
from omni.registry.roles import ROLE_SPECS, family, files, has_family, runner
from omni.supervisor import guard

# what a Hub call can raise that a route should report as a bad gateway, not a crash
ERRORS = (HfHubHTTPError, OSError)

CACHE_TTL_SECONDS = 30.0
_installed_cache: tuple[float, list[dict]] | None = None
# repo id -> (file path in its snapshot, bytes on disk)
_cached_files: dict[str, list[tuple[str, int]]] = {}


def installed(force: bool = False) -> list[dict]:
    """Model repos in the HF cache. Re-walking the whole cache is expensive, so the result is cached briefly."""
    global _installed_cache, _cached_files
    now = time.monotonic()
    if not force and _installed_cache is not None and now - _installed_cache[0] < CACHE_TTL_SECONDS:
        return _installed_cache[1]
    try:
        cache = scan_cache_dir()
    except CacheNotFound:  # nothing has been downloaded on this machine yet
        return []
    models = [{"name": repo.repo_id, "size_bytes": repo.size_on_disk}
              for repo in cache.repos if repo.repo_type == "model"]
    # built aside and swapped in whole: the event loop reads it while this runs in a thread
    _cached_files = {repo.repo_id: [(str(file.file_path.relative_to(revision.snapshot_path)), file.size_on_disk)
                                    for revision in repo.revisions for file in revision.files]
                     for repo in cache.repos}
    _installed_cache = (now, models)
    return models


def _matching(name: str, patterns: list[str] | None) -> bool:
    return patterns is None or any(fnmatch.fnmatch(name, pattern) for pattern in patterns)


def _cached_size(part: "Part") -> int | None:
    """Bytes of `part` on disk when every pattern it names is there, else None."""
    cached = _cached_files.get(part.repo)
    if not cached:
        return None
    if part.files is not None:
        for pattern in part.files:
            if not any(fnmatch.fnmatch(name, pattern) for name, _ in cached):
                return None
    return sum(size for name, size in cached if _matching(name, part.files))


def held_size(role: str, repo_id: str) -> int | None:
    """Bytes a load of `repo_id` reads when everything it needs is cached, else None.

    A diffusers model is a bundle (a GGUF transformer plus the base checkpoint's text encoder and
    VAE), so it counts only once its manifest says what the bundle is and every part is on disk.
    """
    if ROLE_SPECS[role]["engine"] == "diffusers":
        parts = load_manifest(role, repo_id)
    else:
        parts = [Part(repo_id, files(role), 0)]
    if not parts:
        return None
    sizes = [_cached_size(part) for part in parts]
    return None if None in sizes else sum(sizes)


def invalidate() -> None:
    """Forget the cache after a pull or a delete."""
    global _installed_cache
    _installed_cache = None


class Part(NamedTuple):
    """One repo a load reads: the files it takes from it (None for the whole repo) and their size."""
    repo: str
    files: list[str] | None
    size: int


def preferred_gguf(names: list[str]) -> str | None:
    """The GGUF an 8-bit load takes: Q8_0 when the repo ships one, else the first file."""
    gguf_files = [name for name in names if name.endswith(".gguf")]
    eight_bit = [name for name in gguf_files if "Q8_0" in name]
    return (eight_bit or gguf_files or [None])[0]


def base_repo(repo: str) -> str:
    """The checkpoint a GGUF quantizes: the card's `base_model`, else the repo itself."""
    try:
        base = (HfApi().model_info(repo).cardData or {}).get("base_model")
    except Exception:  # noqa: BLE001 - no card or no network: the repo itself is the best guess
        return repo
    if isinstance(base, list):
        base = base[0] if base else None
    return base if isinstance(base, str) and base else repo


def diffusers_files(repo: str) -> tuple[str, list[str]]:
    """The diffusers repo to use and the files of it a GGUF load reads: everything but the transformer weights.

    The GGUF replaces the transformer, so its bf16 shards are never read; the repo's own
    single-file checkpoints (LTX-Video ships several) are skipped the same way.
    """
    from huggingface_hub import hf_hub_download

    try:
        index = json.loads(Path(hf_hub_download(repo, "model_index.json")).read_text())
    except Exception:  # noqa: BLE001 - a card's base_model may name the original, not the diffusers conversion
        repo = f"{repo}-Diffusers"
        index = json.loads(Path(hf_hub_download(repo, "model_index.json")).read_text())
    others = [name for name, value in index.items() if isinstance(value, list) and value[0] and name != "transformer"]
    return repo, ["model_index.json", "transformer/config.json", *(f"{name}/*" for name in others)]


def _pinned(role: str, repo_id: str) -> dict:
    """The config's hand pins (`file`, `base`) when `repo_id` is the role's configured model."""
    section = config.load()[role]
    return section if section["model"] == repo_id else {}


def _size(siblings, patterns: list[str] | None) -> int:
    return sum(sibling.size or 0 for sibling in siblings if _matching(sibling.rfilename, patterns))


def bundle(role: str, repo_id: str) -> list[Part]:
    """Every repo a first load of `repo_id` reads, and which files of each.

    `unsloth/Qwen-Image-2.1-Turbo-GGUF` holds twelve GGUFs (65 GB); the image worker loads one, plus
    the base checkpoint's text encoder and VAE. The speech repos ship fp32, fp16 and int8 ONNX side
    by side; the workers read the int8 ones. Asks the Hub, so call it before downloading, not to load.
    """
    siblings = HfApi().model_info(repo_id, files_metadata=True).siblings or []
    patterns = files(role)
    if patterns and any(_matching(sibling.rfilename, patterns) for sibling in siblings):
        return [Part(repo_id, patterns, _size(siblings, patterns))]
    if ROLE_SPECS[role]["engine"] == "diffusers":
        pinned = _pinned(role, repo_id)
        file = pinned.get("file") or preferred_gguf([sibling.rfilename for sibling in siblings])
        if file is not None:
            base, base_patterns = diffusers_files(pinned.get("base") or base_repo(repo_id))
            base_siblings = HfApi().model_info(base, files_metadata=True).siblings or []
            return [Part(repo_id, [file], _size(siblings, [file])),
                    Part(base, base_patterns, _size(base_siblings, base_patterns))]
    return [Part(repo_id, None, _size(siblings, None))]


def _manifest_path(role: str, repo_id: str) -> Path:
    return config.BUNDLE_DIR / f"{role}--{repo_id.replace('/', '--')}.json"


def save_manifest(role: str, repo_id: str, parts: list[Part]) -> None:
    """Remember what a bundle is, so a later load and the installed check need no network."""
    path = _manifest_path(role, repo_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps([part._asdict() for part in parts], indent=2))
    os.replace(temporary, path)


def load_manifest(role: str, repo_id: str) -> list[Part] | None:
    try:
        raw = json.loads(_manifest_path(role, repo_id).read_text())
        return [Part(entry["repo"], entry["files"], entry["size"]) for entry in raw]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def cache_dir(repo_id: str) -> Path:
    return Path(constants.HF_HUB_CACHE) / f"models--{repo_id.replace('/', '--')}"


def folder_size(folder: Path) -> int:
    """Bytes downloaded so far. HF keeps the real blobs under `blobs/`, so walk only that when it exists.

    A finished blob can be a link into a store shared by every repo, so links are followed and each
    real file is counted once; otherwise the total would fall each time a file completes.
    """
    blobs = folder / "blobs"
    root = blobs if blobs.is_dir() else folder
    if not root.exists():
        return 0
    seen: set[Path] = set()
    total = 0
    for entry in root.rglob("*"):
        try:
            real = entry.resolve()
            if real in seen or not real.is_file():
                continue
            seen.add(real)
            total += real.stat().st_size
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
    filters = dict(ROLE_SPECS[role]["search"])
    markers = family(role)
    terms = query.strip() or (markers[0] if markers else None)
    expand = ["downloads", "likes", "pipeline_tag", "safetensors"]
    if runner(role) == "ollama":
        expand.append("gguf")
    found = HfApi().list_models(search=terms, sort="downloads", limit=limit * 3 if markers else limit,
                                expand=expand, **filters)
    results = []
    for repo in found:
        if not has_family(role, repo.id):
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
    for revision in revisions:
        # the decide worker's hard links to these files would otherwise keep the disk in use
        shutil.rmtree(config.HOME / "decide" / revision, ignore_errors=True)
    for path in config.BUNDLE_DIR.glob(f"*--{repo_id.replace('/', '--')}.json"):
        path.unlink(missing_ok=True)
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
