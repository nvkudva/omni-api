"""Spawning, stopping and measuring the worker processes, and cleaning up leftovers.

The pidfile records the command line as well as the pid, so a stale pidfile whose pid has been
recycled by an unrelated process is never signalled.
"""
import contextlib
import itertools
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
from omni.supervisor import guard

if TYPE_CHECKING:
    from omni.supervisor.worker import Worker

log = logging.getLogger("omni.supervisor")

STOP_GRACE_SECONDS = 5.0
# how long to wait before retrying a worker whose command could not be executed
SPAWN_RETRY_SECONDS = 30.0

# Distinguishes this gateway's worker generations, so each binds a socket of its own.
_generations = itertools.count(1)

# psutil.Process objects are kept between calls because cpu_percent measures since the previous call.
_tracked: dict[int, psutil.Process] = {}


def spawn(worker: "Worker") -> None:
    """Start the worker in its own process group, logging to logs/<name>.log.

    A UDS worker binds a fresh socket per generation and the stable `<role>.sock` link is repointed at
    it, so no generation can unlink a socket another generation is still serving on.
    """
    if worker.process is not None and worker.process.poll() is None:
        log.warning("%s is already running (pid %s); not spawning again", worker.name, worker.process.pid)
        return
    if worker.uds_link is not None:
        token = f"{os.getpid()}.{next(_generations)}"
        worker.uds = config.socket_target(worker.name, token)
        argv = [part.replace("{uds}", str(worker.uds)) for part in worker.argv]
    else:
        worker.uds = None
        argv = list(worker.argv)
    worker.command = guard.command(argv)
    if worker.uds is not None:
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
            worker.command,
            stdin=subprocess.DEVNULL,
            stdout=logfile,
            stderr=subprocess.STDOUT,
            env={**os.environ, **worker.env},
            start_new_session=True,
        )
    except OSError as error:
        # a missing binary (e.g. ollama not installed) must not take the gateway down
        log.error("cannot start %s (%s): %s", worker.name, worker.command[0], error)
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
    _publish_link(worker)
    _write_pidfile(worker)
    worker.status, worker.failed_probes = "starting", 0
    worker.started_at = time.monotonic()
    log.info("started %s (pid %s)", worker.name, worker.process.pid)


def terminate(worker: "Worker") -> None:
    """Stop the worker's whole process group, so helpers it forked go with it."""
    if worker.process is not None:
        _kill_group(worker.process.pid, wait=worker.process.wait)
    worker.process = None
    worker.ps = None
    worker.status = "stopped"
    worker.pidfile.unlink(missing_ok=True)
    if worker.uds is not None:
        worker.uds.unlink(missing_ok=True)
    _unpublish_link(worker)
    worker.uds = None


def _publish_link(worker: "Worker") -> None:
    """Point <role>.sock at this generation's socket, atomically, so clients never see it missing."""
    if worker.uds_link is None or worker.uds is None:
        return
    temporary = worker.uds_link.with_name(f".{worker.uds_link.name}.{os.getpid()}.tmp")
    try:
        temporary.unlink(missing_ok=True)
        os.symlink(worker.uds, temporary)
        os.replace(temporary, worker.uds_link)  # atomic rename over any link a previous generation left
    except OSError:
        temporary.unlink(missing_ok=True)
        log.exception("cannot publish %s", worker.uds_link)


def _unpublish_link(worker: "Worker") -> None:
    """Remove <role>.sock only while it still points at the socket this generation bound."""
    link = worker.uds_link
    if link is None or worker.uds is None:
        return
    try:
        if link.is_symlink() and Path(os.readlink(link)) == worker.uds:
            link.unlink(missing_ok=True)
    except OSError:
        log.exception("cannot remove %s", link)


def _write_pidfile(worker: "Worker") -> None:
    assert worker.process is not None
    # the argv the process was really started with, so a recycled pid is never signalled
    payload = {"pid": worker.process.pid, "argv": worker.command or worker.argv, "owner": os.getpid()}
    try:
        worker.pidfile.write_text(json.dumps(payload))
    except OSError:
        log.exception("cannot write %s", worker.pidfile)


def _kill_group(pgid: int, wait=None) -> None:
    if pgid <= 0:
        return
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
    if pgid <= 0:
        return False
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


def _matches(command: str, argv: list[str]) -> bool:
    parts = [part for part in argv if part]
    return bool(parts) and all(part in command for part in parts)


def is_ours(pid: int, argv: list[str]) -> bool:
    """True only when the process's command line still matches the argv we recorded at spawn."""
    command = command_line(pid)
    return bool(command) and _matches(command, argv)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _read_pidfile(path: Path) -> tuple[int, list[str], int | None] | None:
    try:
        raw = path.read_text()
    except OSError:
        return None
    try:
        record = json.loads(raw)
        owner = record.get("owner")
        return int(record["pid"]), list(record.get("argv", [])), owner if isinstance(owner, int) else None
    except (ValueError, KeyError, TypeError):
        with contextlib.suppress(ValueError):
            return int(raw), [], None  # a pidfile from an older version, with no argv to check
    return None


def kill_leftovers() -> list[str]:
    """Stop worker groups recorded by a gateway that died without cleaning up."""
    stopped = []
    touched = []  # roles whose socket files this start may safely remove
    config.RUN_DIR.mkdir(parents=True, exist_ok=True)
    for pidfile in config.RUN_DIR.glob("*.pid"):
        if pidfile == config.PIDFILE:
            continue
        record = _read_pidfile(pidfile)
        if record is None:
            pidfile.unlink(missing_ok=True)
            touched.append(pidfile.stem)  # nothing is left to protect
            continue
        pid, argv, owner = record
        if owner is not None and _pid_alive(owner):
            continue  # a running gateway still owns this worker
        if not _group_alive(pid):
            pidfile.unlink(missing_ok=True)
            touched.append(pidfile.stem)  # the worker is already gone; its socket is stale
            continue
        command = command_line(pid)
        if command and not _matches(command, argv):
            log.warning("pid %s recorded in %s is not our worker any more; leaving it alone", pid, pidfile.name)
            continue
        if command and not argv:
            log.warning("cannot verify pid %s from %s; leaving it alone", pid, pidfile.name)
            continue
        _kill_group(pid)  # with no command line the leader is gone but forked helpers keep the group alive
        pidfile.unlink(missing_ok=True)
        stopped.append(pidfile.stem)
        touched.append(pidfile.stem)
    _clean_sockets(touched)
    return stopped


def _clean_sockets(touched: list[str]) -> None:
    """Drop the sockets and links of workers this start stopped, or found already gone.

    uvicorn never unlinks its own --uds, so a gateway that died without cleanup leaves both a socket
    file and the link pointing at it behind. Sockets of a live process we chose to leave alone are kept.
    """
    for name in touched:
        for path in config.RUN_DIR.glob(f"{name}.*.sock"):
            path.unlink(missing_ok=True)
    for path in config.RUN_DIR.glob("*.sock"):
        # a link whose target still exists points at a live generation; anything else is a leftover
        if not path.is_symlink() or path.exists():
            continue
        path.unlink(missing_ok=True)


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
