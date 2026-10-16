"""Start, health-check, back off and restart the worker processes."""
import asyncio
import contextlib
import logging
import time

import httpx

from omni import config
from omni.supervisor import process
from omni.supervisor.worker import Worker

log = logging.getLogger("omni.supervisor")

CHECK_SECONDS = 2.0
MAX_BACKOFF_SECONDS = 60.0
STABLE_SECONDS = 60.0
FAILED_PROBES_BEFORE_RESTART = 5


class Supervisor:
    """Owns one process per worker. Every state change takes that worker's lock, so the watch loop
    and an admin restart can never spawn two processes for the same worker."""

    def __init__(self, workers: list[Worker]):
        self.workers = {worker.name: worker for worker in workers}
        self._monitor: asyncio.Task | None = None

    async def start(self) -> None:
        config.ensure_dirs()
        process.kill_leftovers()
        for worker in self.workers.values():
            async with worker.lock:
                process.spawn(worker)
        self._monitor = asyncio.create_task(self._watch())

    async def stop(self) -> None:
        if self._monitor:
            self._monitor.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._monitor
            self._monitor = None
        await asyncio.gather(*(self._stop(worker) for worker in self.workers.values()))
        for worker in self.workers.values():
            await worker.client.aclose()

    async def _stop(self, worker: Worker) -> None:
        async with worker.lock:
            await asyncio.to_thread(process.terminate, worker)

    async def restart(self, name: str) -> None:
        """Start a worker afresh, so it rereads config (a new default model, say)."""
        worker = self.workers[name]
        async with worker.lock:
            await asyncio.to_thread(process.terminate, worker)
            worker.restarts, worker.held = 0, False
            process.spawn(worker)

    async def hold(self, name: str) -> None:
        """Stop a worker and keep it stopped until `restart`."""
        worker = self.workers[name]
        async with worker.lock:
            worker.held = True
            await asyncio.to_thread(process.terminate, worker)

    def status(self) -> dict[str, dict]:
        statuses = {}
        for worker in self.workers.values():
            rss, cpu = process.usage(worker.ps)
            ready = worker.status == "ready"
            statuses[worker.name] = {
                "status": worker.status,
                "pid": worker.process.pid if worker.process else None,
                "restarts": worker.restarts,
                "rss_bytes": rss,
                "cpu_percent": cpu,
                "held": worker.held,
                "model": worker.model if ready else "",
                "loaded": ready and worker.loaded,
            }
        return statuses

    async def _watch(self) -> None:
        while True:
            await asyncio.gather(*(self._check(worker) for worker in self.workers.values()))
            await asyncio.sleep(CHECK_SECONDS)

    async def _check(self, worker: Worker) -> None:
        if worker.held:
            return
        async with worker.lock:
            if worker.held:
                return
            now = time.monotonic()
            if worker.process is None:
                if now >= worker.next_start:
                    process.spawn(worker)
                return
            exit_code = worker.process.poll()
            if exit_code is not None:
                log.warning("%s exited with %s", worker.name, exit_code)
                await asyncio.to_thread(process.terminate, worker)
                self._schedule_restart(worker, now)
                return
            if await self._healthy(worker):
                if worker.status != "ready":
                    log.info("%s ready", worker.name)
                    worker.status, worker.ready_since = "ready", now
                worker.failed_probes = 0
                if now - worker.ready_since > STABLE_SECONDS:
                    worker.restarts = 0
                return
            if worker.status != "ready":
                return
            worker.failed_probes += 1
            if worker.failed_probes >= FAILED_PROBES_BEFORE_RESTART:
                log.warning("%s stopped answering health checks", worker.name)
                await asyncio.to_thread(process.terminate, worker)
                self._schedule_restart(worker, now)

    async def _healthy(self, worker: Worker) -> bool:
        try:
            response = await worker.client.get(worker.health_path, timeout=worker.timeout)
        except httpx.HTTPError:
            return False
        if response.status_code != 200:
            return False
        # keep the worker's own report (model name, whether it is in memory) so the UI can show it
        try:
            report = response.json()
        except ValueError:
            return True
        if isinstance(report, dict):
            if report.get("model"):
                worker.model = str(report["model"])
            worker.loaded = bool(report.get("loaded", worker.loaded))
        return True

    def _schedule_restart(self, worker: Worker, now: float) -> None:
        delay = min(2.0**worker.restarts, MAX_BACKOFF_SECONDS)
        worker.restarts += 1
        worker.next_start = now + delay
        worker.status = "restarting"
        log.info("restarting %s in %.0fs", worker.name, delay)
