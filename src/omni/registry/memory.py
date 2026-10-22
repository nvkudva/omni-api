"""Memory, swap, disk, CPU and load, plus the warnings that say whether another model fits."""
import os
import shutil

import httpx
import psutil

from omni.registry.huggingface import disk_usage_root

GB = 1024**3
LOW_MEMORY_FRACTION = 0.15
CPU_OVERLOAD_PERCENT = 90
WEIGHTS_SHARE_OF_RAM = 0.75


def _gb(size: int) -> str:
    return f"{size / GB:.1f} GB"


def fit_blockers(size_bytes: int) -> list[str]:
    """Reasons a model of this many bytes may not fit on this machine right now.

    These are about *this* model, so they are worth a confirmation; a person may still force it.
    """
    warnings = []
    ram = psutil.virtual_memory()
    disk_free = shutil.disk_usage(disk_usage_root()).free
    if size_bytes > disk_free:
        warnings.append(f"needs {_gb(size_bytes)} of disk; only {_gb(disk_free)} free")
    if size_bytes > ram.total * WEIGHTS_SHARE_OF_RAM:
        warnings.append(f"{_gb(size_bytes)} of weights on a {_gb(ram.total)} machine: expect heavy swapping, "
                        "or a crash if it loads all at once")
    else:
        shortfall = ram_shortfall(size_bytes)
        if shortfall:
            warnings.append(shortfall)
    return warnings


def ram_shortfall(size_bytes: int) -> str | None:
    """Why weights of this size do not fit in the RAM free right now, or None when they do."""
    available = psutil.virtual_memory().available
    if size_bytes <= available:
        return None
    return (f"{_gb(size_bytes)} of weights but only {_gb(available)} RAM free now: "
            "close apps or let other models unload first")


def overload_advisories(cpu: float | None = None) -> list[str]:
    """Machine-wide pressure. Worth showing, but never a reason to refuse loading one model.

    These describe the machine, not the model: they are the same whatever you load, so gating a load
    on them would put a confirmation in front of every single load.
    """
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


async def memory(client: httpx.AsyncClient, workers: dict[str, dict]) -> dict:
    """A snapshot of the machine and of Ollama's resident models, with overload warnings."""
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
        "disk_free_bytes": shutil.disk_usage(disk_usage_root()).free,
        "ollama": ollama,
        "cpu_percent": cpu,
        "cpu_count": os.cpu_count(),
        "load_average": os.getloadavg(),
        "workers": workers,
        "warnings": overload_advisories(cpu),
    }
