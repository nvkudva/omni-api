"""Spawning, stopping and measuring the worker processes, and cleaning up leftovers.

The pidfile records the command line as well as the pid, so a stale pidfile whose pid has been
recycled by an unrelated process is never signalled.
"""
import contextlib
import json
import logging
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING

import psutil

from omni import config

if TYPE_CHECKING:
    from omni.supervisor.worker import Worker

log = logging.getLogger("omni.supervisor")

STOP_GRACE_SECONDS = 5.0
# how long to wait before retrying a worker whose command could not be executed
SPAWN_RETRY_SECONDS = 30.0

# psutil.Process objects are kept between calls because cpu_percent measures since the previous call.
_tracked: dict[int, psutil.Process] = {}


def spawn(worker: "Worker") -> None:
    """Start the worker in its own process group, logging to logs/<name>.log."""
    if worker.process is not None and worker.process.poll() is None:
        log.warning("%s is already running (pid %s); not spawning again", worker.name, worker.process.pid)
        return
    if worker.uds:
        worker.uds.unlink(missing_ok=True)
    logfile_path = config.LOG_DIR / f"{worker.name}.log"
    try:
        logfile = logfile_path.open("ab")
    except OSError:
        log.exception("cannot open %s; %s will not start", logfile_path, worker.name)
        worker.status = "stopped"
        return
    try:
        worker.process = subprocess.Popen(
            worker.argv,
            stdin=subprocess.DEVNULL,
            stdout=logfile,
            stderr=subprocess.STDOUT,
            env={**os.environ, **worker.env},
            start_new_session=True,
        )
    except OSError as error:
        # a missing binary (e.g. ollama not installed) must not take the gateway down
        log.error("cannot start %s (%s): %s", worker.name, worker.argv[0], error)
        worker.process = None
        worker.status = "failed"
        worker.next_start = time.monotonic() + SPAWN_RETRY_SECONDS
        return
    finally:
        logfile.close()
    try:
        worker.ps = psutil.Process(worker.process.pid)
    except psutil.Error:
        worker.ps = None
    _write_pidfile(worker)
    worker.status, worker.failed_probes = "starting", 0
    log.info("started %s (pid %s)", worker.name, worker.process.pid)


def terminate(worker: "Worker") -> None:
    """Stop the worker's whole process group, so helpers it forked go with it."""
    if worker.process is not None:
        _kill_group(worker.process.pid, wait=worker.process.wait)
    worker.process = None
    worker.ps = None
    worker.status = "stopped"
    worker.pidfile.unlink(missing_ok=True)
    if worker.uds:
        worker.uds.unlink(missing_ok=True)


def _write_pidfile(worker: "Worker") -> None:
    assert worker.process is not None
    payload = {"pid": worker.process.pid, "argv": worker.argv}
    try:
        worker.pidfile.write_text(json.dumps(payload))
    except OSError:
        log.exception("cannot write %s", worker.pidfile)


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


def command_line(pid: int) -> str:
    """The command line of a running process, or "" when it is gone."""
    try:
        result = subprocess.run(["ps", "-o", "command=", "-p", str(pid)],
                                capture_output=True, text=True, check=False, timeout=5.0)
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout if result.returncode == 0 else ""


def is_ours(pid: int, argv: list[str]) -> bool:
    """True only when the process's command line still matches the argv we recorded at spawn."""
    command = command_line(pid)
    if not command:
        return False
    parts = [part for part in argv if part]
    return bool(parts) and all(part in command for part in parts)


def _read_pidfile(path: Path) -> tuple[int, list[str]] | None:
    try:
        raw = path.read_text()
    except OSError:
        return None
    try:
        record = json.loads(raw)
        return int(record["pid"]), list(record.get("argv", []))
    except (ValueError, KeyError, TypeError):
        with contextlib.suppress(ValueError):
            return int(raw), []  # a pidfile from an older version, with no argv to check
    return None


def kill_leftovers() -> list[str]:
    """Stop worker groups recorded by a gateway that died without cleaning up."""
    stopped = []
    config.RUN_DIR.mkdir(parents=True, exist_ok=True)
    for pidfile in config.RUN_DIR.glob("*.pid"):
        if pidfile == config.PIDFILE:
            continue
        record = _read_pidfile(pidfile)
        pidfile.unlink(missing_ok=True)
        if record is None:
            continue
        pid, argv = record
        if not _group_alive(pid):
            continue
        if argv and not is_ours(pid, argv):
            log.warning("pid %s recorded in %s is not our worker any more; leaving it alone", pid, pidfile.name)
            continue
        if not argv:
            log.warning("cannot verify pid %s from %s; leaving it alone", pid, pidfile.name)
            continue
        _kill_group(pid)
        stopped.append(pidfile.stem)
    return stopped


def _prune() -> None:
    for pid in list(_tracked):
        if not _tracked[pid].is_running():
            _tracked.pop(pid, None)


def usage(root: psutil.Process | None) -> tuple[int, float]:
    """Resident memory and CPU percent of a worker and the helpers it forked."""
    if root is None:
        return 0, 0.0
    _prune()
    try:
        members = [root]
        for child in root.children(recursive=True):
            member = _tracked.get(child.pid) or psutil.Process(child.pid)
            _tracked[child.pid] = member
            members.append(member)
        rss = 0
        cpu = 0.0
        for member in members:
            try:
                rss += member.memory_info().rss
                cpu += member.cpu_percent(interval=None)
            except psutil.Error:
                continue
    except psutil.Error:
        return 0, 0.0
    return rss, round(cpu, 1)
