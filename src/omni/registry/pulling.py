"""Server-owned downloads with progress any client can watch.

A download runs in its own background task from the moment anything starts it — an explicit
pull, a load that downloads first, or a first inference — so closing the tab, navigating away
or closing the browser never cancels it. Progress lives in ACTIVE while it runs and its outcome
in DONE afterwards; read them with snapshot() and recent(). Attaching to a download only
watches it: dropping the watcher changes nothing about the download itself.
"""
import asyncio
import json
import time
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


ACTIVE: dict[str, dict] = {}
"""Live downloads by repo id: each entry holds role, model, status, total, completed, started_at
and warnings."""


def snapshot() -> list[dict]:
    """The downloads still in flight, oldest first."""
    return sorted(ACTIVE.values(), key=lambda entry: entry["started_at"])


def track(role: str, model: str, total: float | None, completed: float) -> None:
    """Publish one progress sample for an in-flight download."""
    previous = ACTIVE.get(model, {})
    ACTIVE[model] = {"role": role, "model": model, "status": f"downloading {model}",
                     "total": total, "completed": completed,
                     "started_at": previous.get("started_at", time.monotonic()),
                     "warnings": previous.get("warnings", [])}


def warn(role: str, model: str, warning: str) -> None:
    """Publish a fit warning (disk or memory) for an in-flight download, for any watcher."""
    if model not in ACTIVE:
        track(role, model, None, 0)
    ACTIVE[model]["warnings"] = [*ACTIVE[model]["warnings"], warning]


def finish(model: str) -> None:
    """Drop a download from the live set once it lands or fails."""
    ACTIVE.pop(model, None)


TASKS: dict[str, asyncio.Task] = {}
"""The background download per repo id. Owned by this module, never by a watching request."""

DONE: dict[str, dict] = {}
"""Terminal outcome per repo id: role, model, finished_at, plus success or the error event."""

RECENT_TTL_SECONDS = 300.0
MAX_DONE = 20


def progress(model: str) -> dict | None:
    """The latest sample for one in-flight download, if it is still running."""
    return ACTIVE.get(model)


def outcome(model: str) -> dict | None:
    """The terminal outcome of a finished download, if one is remembered."""
    return DONE.get(model)


def recent() -> list[dict]:
    """Finished downloads still worth reporting, newest first."""
    now = time.monotonic()
    for model in [name for name, entry in DONE.items()
                  if now - entry.get("finished_at", 0) > RECENT_TTL_SECONDS]:
        del DONE[model]
    return sorted(DONE.values(), key=lambda entry: entry["finished_at"], reverse=True)[:MAX_DONE]


async def _background(role: str, model: str, client: httpx.AsyncClient) -> None:
    """Drain one download to completion, owned by nobody watching it."""
    terminal: dict = {"error": "download interrupted"}
    warnings: list[str] = []
    try:
        async for event in pull(client, role, model):
            if event.get("warning"):
                warnings.append(event["warning"])
                warn(role, model, event["warning"])
            if event.get("error") or event.get("status") == "success":
                terminal = event
    except asyncio.CancelledError:
        # stop() cancels this task on purpose; record it as the outcome, so watchers and loads see it
        terminal = {"error": f"download of {model} stopped", "cancelled": True}
    except Exception as error:  # noqa: BLE001 - the outcome must be recorded, not raised
        terminal = {"error": f"download failed: {error}"}
    finally:
        TASKS.pop(model, None)
    terminal.pop("started_at", None)
    DONE[model] = {"role": role, "model": model, **terminal, "warnings": warnings,
                   "finished_at": time.monotonic()}
    while len(DONE) > MAX_DONE:
        oldest = min(DONE, key=lambda name: DONE[name]["finished_at"])
        del DONE[oldest]


def ensure_started(role: str, model: str, client: httpx.AsyncClient) -> asyncio.Task:
    """Start the download unless it is already running, and hand back its task.

    Watching the task (or dropping it when a request goes away) never affects the download:
    it completes on its own and records its outcome in DONE.
    """
    task = TASKS.get(model)
    if task is None or task.done():
        task = TASKS[model] = asyncio.create_task(_background(role, model, client),
                                                  name=f"omni-download-{model}")
    return task


def stop(model: str) -> bool:
    """Cancel one in-flight download; whether there was one. Partial files stay, so a retry resumes."""
    task = TASKS.get(model)
    if task is None or task.done():
        return False
    task.cancel()
    return True


async def shutdown() -> None:
    """Cancel every in-flight download and wait until its child process is gone."""
    tasks = list(TASKS.values())
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    TASKS.clear()


async def pull(client: httpx.AsyncClient, role: str, model: str) -> AsyncIterator[dict]:
    """Progress events while `model` downloads."""
    events = _ollama_pull(client, role, model) if runner(role) == "ollama" else _hf_pull(role, model)
    try:
        async for event in events:
            if "network is unreachable" in event.get("error", ""):
                event = {"error": event["error"] + UNREACHABLE_HINT}
            yield event
    finally:
        finish(model)


async def _ollama_pull(client: httpx.AsyncClient, role: str, model: str) -> AsyncIterator[dict]:
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
                if not isinstance(event, dict):
                    continue
                total = event.get("total")
                # the biggest layer is the weights; its size is the first sign of whether they fit
                if not warned and isinstance(total, (int, float)) and total > SIZE_WARNING_THRESHOLD:
                    warned = True
                    for warning in fit_blockers(total):
                        yield {"warning": warning}
                if isinstance(total, (int, float)):
                    track(role, model, total, event.get("completed") or 0)
                yield event
    except httpx.HTTPError as error:
        yield {"error": f"ollama: {error}"}


async def _download_all(targets: list[huggingface.Part]) -> None:
    for repo, files, _ in targets:
        await huggingface.download(repo, files)


async def _hf_pull(role: str, repo_id: str) -> AsyncIterator[dict]:
    try:
        targets = await asyncio.to_thread(huggingface.bundle, role, repo_id)
    except (HfHubHTTPError, OSError, ValueError) as error:
        yield gated_event(repo_id) if is_gated(error) else {"error": f"huggingface: {error}"}
        return
    total = sum(size for _, _, size in targets)
    for warning in fit_blockers(total):
        yield {"warning": warning}
    track(role, repo_id, total, 0)
    download = asyncio.create_task(_download_all(targets))
    try:
        while not download.done():
            completed = 0
            for repo, _, size in targets:
                completed += min(size, await asyncio.to_thread(huggingface.folder_size, huggingface.cache_dir(repo)))
            track(role, repo_id, total, completed)
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
        await asyncio.gather(download, return_exceptions=True)
    huggingface.save_manifest(role, repo_id, targets)
    huggingface.invalidate()
    yield {"status": "success"}
