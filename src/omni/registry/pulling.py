"""Download progress as NDJSON events, for either runtime, ending in {"status": "success"} or {"error": ...}."""
import asyncio
import json
from collections.abc import AsyncIterator

import httpx
from huggingface_hub.errors import GatedRepoError, HfHubHTTPError

from omni.registry import huggingface
from omni.registry.memory import fit_blockers
from omni.registry.roles import runner

PROGRESS_SECONDS = 2.0
SIZE_WARNING_THRESHOLD = 100 * 1024**2
# Ollama's blob host resolves to IPv6 first; a Mac with an IPv6 address but no IPv6 route fails every pull
UNREACHABLE_HINT = (" (no route to the download host: if the address above is IPv6, this network hands out"
                    " IPv6 but does not route it; set Configure IPv6 to Link-local only in System Settings"
                    " > Network > Details > TCP/IP, or fix IPv6 on the router)")


def accept_url(repo_id: str) -> str:
    return f"https://huggingface.co/{repo_id}"


def is_gated(error: Exception) -> bool:
    if isinstance(error, GatedRepoError):
        return True
    text = str(error).lower()
    return "gated repo" in text or "not in the authorized list" in text


def gated_event(repo_id: str) -> dict:
    return {"error": f"{repo_id} needs its terms accepted on Hugging Face before it can download.",
            "code": "gated", "model": repo_id, "accept_url": accept_url(repo_id)}


async def pull(client: httpx.AsyncClient, role: str, model: str) -> AsyncIterator[dict]:
    """Progress events while `model` downloads."""
    events = _ollama_pull(client, model) if runner(role) == "ollama" else _hf_pull(model)
    async for event in events:
        if "network is unreachable" in event.get("error", ""):
            event = {"error": event["error"] + UNREACHABLE_HINT}
        yield event


async def _ollama_pull(client: httpx.AsyncClient, model: str) -> AsyncIterator[dict]:
    warned = False
    try:
        async with client.stream("POST", "/api/pull", json={"model": model}) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                # the biggest layer is the weights; its size is the first sign of whether they fit
                if not warned and event.get("total", 0) > SIZE_WARNING_THRESHOLD:
                    warned = True
                    for warning in fit_blockers(event["total"]):
                        yield {"warning": warning}
                yield event
    except httpx.HTTPError as error:
        yield {"error": f"ollama: {error}"}


async def _hf_pull(repo_id: str) -> AsyncIterator[dict]:
    try:
        total = await asyncio.to_thread(huggingface.size, repo_id)
    except (HfHubHTTPError, OSError) as error:
        yield gated_event(repo_id) if is_gated(error) else {"error": f"huggingface: {error}"}
        return
    for warning in fit_blockers(total):
        yield {"warning": warning}
    download = asyncio.create_task(asyncio.to_thread(huggingface.download, repo_id))
    folder = huggingface.cache_dir(repo_id)
    try:
        while not download.done():
            completed = await asyncio.to_thread(huggingface.folder_size, folder)
            yield {"status": f"downloading {repo_id}", "total": total, "completed": completed}
            await asyncio.wait({download}, timeout=PROGRESS_SECONDS)
        download.result()
    except (HfHubHTTPError, OSError) as error:
        yield gated_event(repo_id) if is_gated(error) else {"error": f"huggingface: {error}"}
        return
    finally:
        # a client that disconnects mid-download closes this generator; do not leave the task pending
        if not download.done():
            download.cancel()
    huggingface.invalidate()
    yield {"status": "success"}
