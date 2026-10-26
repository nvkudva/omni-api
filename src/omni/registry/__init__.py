"""One place to ask about roles, installed models and the machine.

The gateway imports from here; the submodules hold the runtime-specific detail.
"""
import asyncio
import time

import httpx

from omni.registry.huggingface import (
    ERRORS as HF_ERRORS,
)
from omni.registry.huggingface import (
    cache_dir,
    holds,
    folder_size,
    invalidate,
    search,
)
from omni.registry.huggingface import (
    delete as hf_delete,
)
from omni.registry.huggingface import (
    download_plan as hf_plan,
)
from omni.registry.huggingface import (
    installed as hf_installed,
)
from omni.registry.memory import (
    fit_blockers,
    memory,
    overload_advisories,
    ram_shortfall,
)
from omni.registry.ollama import (
    capabilities,
)
from omni.registry.ollama import (
    delete as ollama_delete,
)
from omni.registry.ollama import (
    installed as ollama_installed,
)
from omni.registry.ollama import (
    load as ollama_load,
)
from omni.registry.ollama import (
    unload as ollama_unload,
)
from omni.registry.pulling import (
    ensure_started,
    outcome,
    progress,
    pull,
    recent,
    snapshot,
)
from omni.registry.roles import (
    HF_ROLES,
    OLLAMA_ROLES,
    PYTHON_WORKERS,
    ROLES,
    bare_repo_id,
    canonical,
    family,
    matches,
    normalize_repo_id,
    repo_name,
    require,
    runner,
    supports,
    unavailable_reason,
)

__all__ = [
    "HF_ERRORS",
    "HF_ROLES",
    "OLLAMA_ROLES",
    "PYTHON_WORKERS",
    "ROLES",
    "bare_repo_id",
    "cache_dir",
    "canonical",
    "capabilities",
    "ensure_started",
    "family",
    "fit_blockers",
    "folder_size",
    "hf_delete",
    "hf_installed",
    "hf_plan",
    "installed_for",
    "invalidate",
    "matches",
    "memory",
    "normalize_repo_id",
    "ollama_capabilities",
    "ollama_delete",
    "ollama_installed",
    "ollama_load",
    "ollama_unload",
    "outcome",
    "overload_advisories",
    "progress",
    "pull",
    "ram_shortfall",
    "recent",
    "repo_name",
    "require",
    "runner",
    "search",
    "snapshot",
    "supports",
    "unavailable_reason",
]


CAPABILITY_TTL_SECONDS = 60.0
_capability_cache: dict[str, tuple[float, list[str]]] = {}


async def ollama_capabilities(client: httpx.AsyncClient, names: list[str]) -> list[list[str]]:
    """Each model's Ollama capabilities, asking /api/show only for names not seen lately."""
    now = time.monotonic()
    stale = [name for name in names
             if name not in _capability_cache or now - _capability_cache[name][0] >= CAPABILITY_TTL_SECONDS]
    found = await asyncio.gather(*(capabilities(client, name) for name in stale))
    for name, caps in zip(stale, found):
        if caps:
            _capability_cache[name] = (now, caps)
    return [_capability_cache[name][1] if name in _capability_cache else [] for name in names]


async def installed_for(role: str, client: httpx.AsyncClient) -> list[dict]:
    """Models this role could actually load: Ollama's list, or cached repos its runtime supports."""
    if runner(role) != "ollama":
        cached = await asyncio.to_thread(hf_installed)
        return [model for model in cached if supports(role, model["name"]) and holds(role, model["name"])]
    models = await ollama_installed(client)
    found = await ollama_capabilities(client, [model["name"] for model in models])
    usable = []
    for model, caps in zip(models, found):
        if not caps:
            continue
        if role == "embed":
            if "embedding" in caps:
                usable.append(model)
        elif "completion" in caps:
            usable.append(model)
    return usable
