"""Keep the roles a person pinned resident, so the first request is never the one that loads.

Nothing here changes what a worker does: a worker drops its model after `idle_unload_minutes` of no
use, and a plain `/load` only resets that clock. So a pinned worker is simply re-loaded before the
window closes. A pinned Ollama model is different — it is held with `keep_alive = -1`, so there is
nothing to refresh and the loop only has to notice if it has gone away.
"""
import asyncio
import contextlib
import logging

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
    """Re-loads every pinned role on a timer, for as long as the gateway runs."""

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
        """One pass over the pinned roles. Returns the ones that were (re)loaded."""
        pinned = config.load_pins()
        self._pending = False
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
        if registry.runner(role) == "ollama":
            held = resident if resident is not None else await residency.ollama_resident(self.ctx)
            if model in held:
                return HELD  # keep_alive = -1 is already holding it
        await residency.set_residency(self.ctx, role, model, True, keep_warm=True)
        return WARMED
