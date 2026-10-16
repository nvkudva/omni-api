"""Download progress as NDJSON events, for either runtime, ending in {"status": "success"} or {"error": ...}."""
import asyncio
import json
from collections.abc import AsyncIterator

import httpx
from huggingface_hub.errors import HfHubHTTPError

from omni.registry import huggingface
from omni.registry.memory import fit_warnings
from omni.registry.roles import runner

PROGRESS_SECONDS = 2.0
SIZE_WARNING_THRESHOLD = 100 * 1024**2


async def pull(client: httpx.AsyncClient, role: str, model: str) -> AsyncIterator[dict]:
    """Progress events while `model` downloads."""
    events = _ollama_pull(client, model) if runner(role) == "ollama" else _hf_pull(model)
    async for event in events:
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
                    for warning in fit_warnings(event["total"]):
                        yield {"warning": warning}
                yield event
    except httpx.HTTPError as error:
        yield {"error": f"ollama: {error}"}


async def _hf_pull(repo_id: str) -> AsyncIterator[dict]:
    try:
        total = await asyncio.to_thread(huggingface.size, repo_id)
    except (HfHubHTTPError, OSError) as error:
        yield {"error": f"huggingface: {error}"}
        return
    for warning in fit_warnings(total):
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
        yield {"error": f"huggingface: {error}"}
        return
    finally:
        # a client that disconnects mid-download closes this generator; do not leave the task pending
        if not download.done():
            download.cancel()
    huggingface.invalidate()
    yield {"status": "success"}
