"""Loading and unloading a role's model, and what — if anything — stands in the way.

The admin routes and the keep-warm loop both go through here, so a pinned model and a hand-loaded one
take exactly the same path, and a refusal is described in one place.
"""
import asyncio
import logging
from typing import NamedTuple

import httpx
from huggingface_hub.errors import HfHubHTTPError

from omni import registry
from omni.registry import pulling

log = logging.getLogger("omni.gateway")

# Ollama reads a negative keep_alive as "never unload"; a pinned Ollama model uses it so it costs
# nothing to hold, instead of being re-loaded on a timer.
OLLAMA_KEEP_WARM = -1
OLLAMA_KEEP_ALIVE = "10m"

HF_ERRORS = (HfHubHTTPError, OSError)


class Plan(NamedTuple):
    """What loading `model` would involve: machine-wide advice, model-specific obstacles, and whether
    the model cannot be loaded at all — the last one because `force: true` would only fail again."""

    advisories: list[str]
    blockers: list[str]
    fatal: str | None


async def installed(ctx, role: str) -> list[dict]:
    """What this role has on disk right now; an unreachable runtime reads as "nothing installed"."""
    try:
        return await registry.installed_for(role, ctx.clients["chat"])
    except (httpx.HTTPError, *HF_ERRORS):
        return []


async def plan(ctx, role: str, model: str) -> Plan:
    """Whether `model` can load for `role`, and what to warn about either way."""
    advisories = registry.overload_advisories()
    available = await installed(ctx, role)
    match = next((entry for entry in available if registry.matches(entry["name"], model)), None)
    if match is not None:
        return Plan(advisories, registry.fit_blockers(match["size_bytes"]), None)
    if registry.runner(role) == "ollama":
        # loading never pulls: Ollama answers "model not found", so this is not force-able
        return Plan(advisories, [], f"{model} is not installed; install it first")
    return Plan(advisories, [f"{model} is not downloaded yet; loading downloads it first"], None)


async def ensure_downloaded(ctx, role: str, model: str) -> None:
    """Download `model` with live progress when it is not on disk yet.

    The download belongs to the server, not to this request: it is awaited behind a shield,
    so a load whose browser tab closed still finishes downloading instead of dying with it.
    """
    available = await installed(ctx, role)
    if any(registry.matches(entry["name"], model) for entry in available):
        return
    task = pulling.ensure_started(role, model, ctx.clients["chat"])
    await asyncio.shield(task)
    terminal = pulling.outcome(model) or {}
    if terminal.get("error"):
        raise OSError(terminal["error"])


async def ensure_ready(ctx, role: str) -> str | None:
    """Download the role's model with live progress when it is not on disk yet.

    Inference used to trigger a silent download inside the worker: the first request sat for
    minutes with no progress bar. Draining the download here publishes every sample to the
    shared tracker first, so the progress shows while the request waits. Returns an error
    message when the download fails, else None.
    """
    try:
        await ensure_downloaded(ctx, role, ctx.settings[role]["model"])
    except OSError as error:
        model = ctx.settings[role]["model"]
        if registry.runner(role) != "ollama" and (registry.cache_dir(model) / "snapshots").is_dir():
            # already cached (the scan missed it, or the hub is unreachable): the worker loads it
            return None
        return f"{role}: {error}"
    return None


async def set_residency(ctx, role: str, model: str, load: bool, *, keep_warm: bool = False) -> bool | None:
    """Load or unload the role's model. Returns whether it now sits in memory, when the runtime says."""
    if registry.runner(role) == "ollama":
        if load:
            keep_alive = OLLAMA_KEEP_WARM if keep_warm else OLLAMA_KEEP_ALIVE
            await registry.ollama_load(ctx.clients["chat"], role, model, keep_alive=keep_alive)
        else:
            await registry.ollama_unload(ctx.clients["chat"], role, model)
        return None
    if load:
        await ensure_downloaded(ctx, role, model)
    response = await ctx.clients[role].post("/load" if load else "/unload", timeout=600.0)
    response.raise_for_status()
    try:
        report = response.json()
    except ValueError:
        return None
    return bool(report.get("loaded")) if isinstance(report, dict) and "loaded" in report else None


async def ollama_resident(ctx) -> set[str]:
    """The model names Ollama currently holds in memory."""
    try:
        response = await ctx.clients["chat"].get("/api/ps", timeout=2.0)
        return {model["name"] for model in response.json().get("models", [])}
    except (httpx.HTTPError, ValueError):
        return set()
