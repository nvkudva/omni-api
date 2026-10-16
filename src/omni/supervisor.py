import asyncio
import contextlib
import logging
import os
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import psutil

from omni import config

log = logging.getLogger("omni.supervisor")

CHECK_SECONDS = 2.0
MAX_BACKOFF_SECONDS = 60.0
STABLE_SECONDS = 60.0
FAILED_PROBES_BEFORE_RESTART = 5
STOP_GRACE_SECONDS = 5.0


@dataclass
class Worker:
    """One supervised child: its command, how to reach it, and its health so far."""

    name: str
    argv: list[str]
    client: httpx.AsyncClient
    health_path: str
    uds: Path | None = None
    env: dict[str, str] = field(default_factory=dict)
    process: subprocess.Popen | None = None
    status: str = "stopped"
    restarts: int = 0
    failed_probes: int = 0
    ready_since: float = 0.0
    next_start: float = 0.0
    held: bool = False

    @property
    def pidfile(self) -> Path:
        return config.RUN_DIR / f"{self.name}.pid"


class Supervisor:
    def __init__(self, workers: list[Worker]):
        self.workers = {worker.name: worker for worker in workers}
        self._monitor: asyncio.Task | None = None

    async def start(self) -> None:
        config.ensure_dirs()
        kill_leftovers()
        for worker in self.workers.values():
            _spawn(worker)
        self._monitor = asyncio.create_task(self._watch())

    async def stop(self) -> None:
        if self._monitor:
            self._monitor.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._monitor
        await asyncio.gather(*(asyncio.to_thread(_terminate, worker) for worker in self.workers.values()))
        for worker in self.workers.values():
            await worker.client.aclose()

    async def restart(self, name: str) -> None:
        """Start a worker afresh, so it rereads config (a new default model, say)."""
        worker = self.workers[name]
        await asyncio.to_thread(_terminate, worker)
        worker.restarts, worker.held = 0, False
        _spawn(worker)

    async def hold(self, name: str) -> None:
        """Stop a worker and keep it stopped until `restart`."""
        worker = self.workers[name]
        worker.held = True
        await asyncio.to_thread(_terminate, worker)

    def status(self) -> dict[str, dict]:
        statuses = {}
        for worker in self.workers.values():
            rss, cpu = _usage(worker.process.pid) if worker.process else (0, 0.0)
            statuses[worker.name] = {
                "status": worker.status,
                "pid": worker.process.pid if worker.process else None,
                "restarts": worker.restarts,
                "rss_bytes": rss,
                "cpu_percent": cpu,
            }
        return statuses

    async def _watch(self) -> None:
        while True:
            await asyncio.gather(*(self._check(worker) for worker in self.workers.values()))
            await asyncio.sleep(CHECK_SECONDS)

    async def _check(self, worker: Worker) -> None:
        now = time.monotonic()
        if worker.held:
            return
        if worker.process is None:
            if now >= worker.next_start:
                _spawn(worker)
            return
        exit_code = worker.process.poll()
        if exit_code is not None:
            log.warning("%s exited with %s", worker.name, exit_code)
            await asyncio.to_thread(_terminate, worker)
            _schedule_restart(worker, now)
            return
        if await _healthy(worker):
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
            await asyncio.to_thread(_terminate, worker)
            _schedule_restart(worker, now)


async def _healthy(worker: Worker) -> bool:
    try:
        response = await worker.client.get(worker.health_path, timeout=2.0)
    except httpx.HTTPError:
        return False
    return response.status_code == 200


_processes: dict[int, psutil.Process] = {}


def _tracked(process: psutil.Process) -> psutil.Process:
    """The same Process object each call, since cpu_percent measures since its previous call."""
    return _processes.setdefault(process.pid, process)


def _usage(pid: int) -> tuple[int, float]:
    """Resident memory and CPU percent of a worker and the helpers it forked."""
    try:
        root = psutil.Process(pid)
        tree = [_tracked(member) for member in (root, *root.children(recursive=True))]
        rss = sum(member.memory_info().rss for member in tree)
        cpu = sum(member.cpu_percent(interval=None) for member in tree)
    except psutil.Error:
        return 0, 0.0
    return rss, round(cpu, 1)


def _spawn(worker: Worker) -> None:
    if worker.uds:
        worker.uds.unlink(missing_ok=True)
    logfile = (config.LOG_DIR / f"{worker.name}.log").open("ab")
    worker.process = subprocess.Popen(
        worker.argv,
        stdin=subprocess.DEVNULL,
        stdout=logfile,
        stderr=subprocess.STDOUT,
        env={**os.environ, **worker.env},
        start_new_session=True,
    )
    logfile.close()
    worker.pidfile.write_text(str(worker.process.pid))
    worker.status, worker.failed_probes = "starting", 0
    log.info("started %s (pid %s)", worker.name, worker.process.pid)


def _schedule_restart(worker: Worker, now: float) -> None:
    delay = min(2.0**worker.restarts, MAX_BACKOFF_SECONDS)
    worker.restarts += 1
    worker.next_start = now + delay
    worker.status = "restarting"
    log.info("restarting %s in %.0fs", worker.name, delay)


def _terminate(worker: Worker) -> None:
    """Stop the worker's whole process group, so helpers it forked go with it."""
    if worker.process is not None:
        _kill_group(worker.process.pid, wait=worker.process.wait)
    worker.process = None
    worker.status = "stopped"
    worker.pidfile.unlink(missing_ok=True)
    if worker.uds:
        worker.uds.unlink(missing_ok=True)


def _kill_group(pgid: int, wait=None) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.killpg(pgid, signal.SIGTERM)
    deadline = time.monotonic() + STOP_GRACE_SECONDS
    while time.monotonic() < deadline:
        if wait is not None:
            with contextlib.suppress(subprocess.TimeoutExpired):
                wait(timeout=0.1)
        else:
            time.sleep(0.1)
        if not _group_alive(pgid):
            return
    with contextlib.suppress(ProcessLookupError):
        os.killpg(pgid, signal.SIGKILL)


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _is_ours(pid: int) -> bool:
    """Guards against a recycled pid: only stop a group whose leader is one of our worker commands."""
    result = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True, check=False)
    command = result.stdout
    return any(marker in command for marker in ("omni.workers", "ollama"))


def kill_leftovers() -> list[str]:
    """Stop worker groups recorded by a gateway that died without cleaning up."""
    stopped = []
    for pidfile in config.RUN_DIR.glob("*.pid"):
        if pidfile == config.PIDFILE:
            continue
        with contextlib.suppress(ValueError):
            pgid = int(pidfile.read_text())
            if _group_alive(pgid) and _is_ours(pgid):
                _kill_group(pgid)
                stopped.append(pidfile.stem)
        pidfile.unlink(missing_ok=True)
    return stopped
