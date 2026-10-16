"""One place to ask about roles, installed models and the machine.

The gateway imports from here; the submodules hold the runtime-specific detail.
"""
import asyncio

import httpx

from omni.registry.huggingface import (
    cache_dir,
    folder_size,
    invalidate,
    search,
)
from omni.registry.huggingface import (
    delete as hf_delete,
)
from omni.registry.huggingface import (
    installed as hf_installed,
)
from omni.registry.huggingface import (
    size as hf_size,
)
from omni.registry.memory import fit_warnings, memory, overload_warnings
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
from omni.registry.pulling import pull
from omni.registry.roles import (
    HF_ROLES,
    OLLAMA_ROLES,
    PYTHON_WORKERS,
    ROLES,
    runner,
    supports,
)

__all__ = [
    "HF_ROLES",
    "OLLAMA_ROLES",
    "PYTHON_WORKERS",
    "ROLES",
    "cache_dir",
    "capabilities",
    "fit_warnings",
    "folder_size",
    "hf_delete",
    "hf_installed",
    "hf_size",
    "installed_for",
    "invalidate",
    "memory",
    "ollama_delete",
    "ollama_installed",
    "ollama_load",
    "ollama_unload",
    "overload_warnings",
    "pull",
    "runner",
    "search",
    "supports",
]


async def installed_for(role: str, client: httpx.AsyncClient) -> list[dict]:
    """Models this role could actually load: Ollama's list, or cached repos its runtime supports."""
    if runner(role) == "ollama":
        return await ollama_installed(client)
    cached = await asyncio.to_thread(hf_installed)
    return [model for model in cached if supports(role, model["name"])]
