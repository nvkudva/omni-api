"""The one owner of what Python workers keep in memory.

Pinned roles are loaded and kept, so the first request is never the one that loads. Every other
worker model is unloaded once it has had no request for `idle_unload_minutes` and is not busy;
workers never unload on their own clock. A pinned Ollama model is held with `keep_alive = -1`, so
the loop only has to notice if it has gone away; unpinned Ollama models age out by their keep_alive.
"""
import asyncio
import contextlib
import logging
import time

from omni import config, registry
from omni.gateway import residency

log = logging.getLogger("omni.gateway")

# the loop ticks fast while something still needs warming, and slowly once everything is held
FAST_SECONDS = 5.0
REFRESH_SECONDS = 30.0

# what one warm() attempt did: it loaded the model, found it already held, or has to try again
WARMED = "warmed"
HELD = "held"
WAITING = "waiting"


class KeepWarm:
    """Loads pinned roles and unloads idle worker models on a timer, for as long as the gateway runs."""

    def __init__(self, ctx):
        self.ctx = ctx
        self._task: asyncio.Task | None = None
        # True while a pinned role is still waiting on its worker, so the loop retries soon
        self._pending = True

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(FAST_SECONDS if self._pending else REFRESH_SECONDS)
            try:
                await self.refresh()
            except Exception:  # a keep-warm pass must never take the gateway down
                log.warning("keep-warm pass failed", exc_info=True)

    async def refresh(self) -> list[str]:
        """One pass: unload idle workers, then load pinned roles. Returns the roles it loaded."""
        pinned = config.load_pins()
        self._pending = False
        await self.release_idle(pinned)
        if not pinned:
            return []
        resident = await residency.ollama_resident(self.ctx)
        warmed = []
        for role in pinned:
            try:
                outcome = await self.warm(role, resident)
            except Exception:
                log.warning("could not keep %s warm", role, exc_info=True)
                self._pending = True
                continue
            if outcome == WARMED:
                warmed.append(role)
            elif outcome == WAITING:
                self._pending = True
        return warmed

    async def warm(self, role: str, resident: set[str] | None = None) -> str:
        """Hold one pinned role's model in memory. Says what it did, so the loop knows to retry."""
        worker = self.ctx.workers.get(role)
        if worker is not None and worker.status != "ready":
            # the watch loop owns starting it; this pass would only race with that
            return WAITING
        model = self.ctx.settings[role]["model"]
        if worker is not None and worker.loaded:
            return HELD
        if registry.runner(role) == "ollama":
            held = resident if resident is not None else await residency.ollama_resident(self.ctx)
            if any(registry.matches(name, model) for name in held):
                return HELD  # keep_alive = -1 is already holding it
        await residency.set_residency(self.ctx, role, model, True, keep_warm=True)
        return WARMED

    async def release_idle(self, pinned: list[str]) -> list[str]:
        """Unload every unpinned worker model that has sat unused past `idle_unload_minutes`."""
        idle_seconds = config.idle_seconds(self.ctx.settings)
        if idle_seconds <= 0:
            return []
        now = time.monotonic()
        released = []
        for role, worker in self.ctx.workers.items():
            if role in pinned or worker.status != "ready" or not worker.loaded or worker.busy:
                continue
            if now - worker.last_used < idle_seconds:
                continue
            try:
                await residency.set_residency(self.ctx, role, self.ctx.settings[role]["model"], False)
            except Exception:
                log.warning("could not unload idle %s", role, exc_info=True)
                continue
            worker.loaded = False
            released.append(role)
        return released
