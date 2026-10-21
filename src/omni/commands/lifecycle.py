"""``omni serve``, ``up``, ``restart``, ``down``, ``status`` and ``logs``: run and supervise the gateway.

The gateway records its own pid in ``omni.pid``. Every operation that signals it first checks the
pid is alive *and* still looks like an omni gateway, so a pidfile whose pid the OS has recycled is
never signalled.
"""
import contextlib
import fcntl
import json
import logging
import os
import signal
import socket
import subprocess
import sys
import time
import webbrowser

import httpx
import psutil

from omni import config, logs, supervisor, version

log = logging.getLogger("omni.commands")

UP_TIMEOUT_SECONDS = 30
DOWN_TIMEOUT_SECONDS = 20
POLL_SECONDS = 0.5
# a live process must contain both of these in its command line to be treated as our gateway
GATEWAY_MARKERS = ("omni", "serve")


def port_in_use(host: str, port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.5)
        return probe.connect_ex((host, port)) == 0


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _read_record() -> dict:
    """The gateway pidfile as a dict. Current files hold pid/host/port; older ones held a bare pid."""
    try:
        raw = config.PIDFILE.read_text().strip()
    except (FileNotFoundError, OSError):
        return {}
    try:
        record = json.loads(raw)
    except ValueError:
        record = None
    if isinstance(record, dict) and "pid" in record:
        return record
    try:
        return {"pid": int(raw)}
    except ValueError:
        return {}


def gateway_pid() -> int | None:
    """The gateway's pid, or None when it is gone or the pid no longer belongs to our gateway."""
    record = _read_record()
    pid = record.get("pid")
    if not isinstance(pid, int) or pid <= 0 or pid == os.getpid():
        return None
    if not _alive(pid):
        return None
    command = supervisor.command_line(pid)
    if not command or not supervisor.is_ours(pid, list(GATEWAY_MARKERS)):
        log.warning("pid %s in %s is no longer an omni gateway; ignoring it", pid, config.PIDFILE.name)
        return None
    return pid


def _wait_gone(pid: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _alive(pid):
            return True
        time.sleep(POLL_SECONDS)
    return not _alive(pid)


def address(gateway: dict) -> tuple[str, int]:
    """The host and port the running gateway bound, falling back to the configured ones."""
    record = _read_record()
    return record.get("host", gateway["host"]), record.get("port", gateway["port"])


def _non_loopback_refusal(host: str) -> str | None:
    """Why `host` cannot serve admin routes, or None when it can."""
    if host in ("127.0.0.1", "localhost", "::1") or os.environ.get("OMNI_ALLOW_NON_LOOPBACK") == "1":
        return None
    return ("omni: refusing non-loopback host (admin routes have no auth); "
            "set OMNI_ALLOW_NON_LOOPBACK=1 to override")


# runs detached from the gateway, so it outlives the `down` that stops the gateway that launched it
RELAUNCH_SCRIPT = 'sleep 1; "$0" -m omni.cli down; if [ "$1" = restart ]; then "$0" -m omni.cli up --host "$2" --port "$3" --no-browser; fi'


def relaunch(host: str, port: int, restart: bool) -> None:
    """Stop the gateway from outside it, and start it again when `restart`. Returns at once."""
    config.ensure_dirs()
    with (config.LOG_DIR / "omni.log").open("ab") as logfile:
        subprocess.Popen(
            ["sh", "-c", RELAUNCH_SCRIPT, sys.executable, "restart" if restart else "stop", host, str(port)],
            stdin=subprocess.DEVNULL, stdout=logfile, stderr=subprocess.STDOUT, start_new_session=True,
        )


@contextlib.contextmanager
def _exclusive_lock():
    """Hold an flock next to omni.pid for the life of the gateway; yields False when another holds it."""
    config.ensure_dirs()
    with config.PIDFILE.with_suffix(".lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        yield True


def serve(host: str, port: int) -> int:
    """Run the gateway in the foreground."""
    refusal = _non_loopback_refusal(host)
    if refusal:
        print(refusal, file=sys.stderr)
        return 1
    with _exclusive_lock() as acquired:
        if not acquired:
            print("omni: another gateway is already starting or running (lock held); `omni status` to check",
                  file=sys.stderr)
            return 1
        return _serve_locked(host, port)


def _serve_locked(host: str, port: int) -> int:
    import uvicorn

    from omni import gateway

    if port_in_use(host, port):
        print(f"omni: {host}:{port} is already in use (another omni?); `omni status` to check", file=sys.stderr)
        return 1
    config.ensure_dirs()
    try:
        app = gateway.create()
    except config.ConfigError as error:
        print(f"omni: bad configuration: {error}", file=sys.stderr)
        return 1
    config.PIDFILE.write_text(json.dumps({"pid": os.getpid(), "host": host, "port": port}))
    try:
        server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="info"))
        # a closed terminal sends SIGHUP; uvicorn ignores it, which would skip the shutdown that stops the workers
        signal.signal(signal.SIGHUP, lambda signum, frame: setattr(server, "should_exit", True))
        server.run()
    finally:
        if _read_record().get("pid") == os.getpid():
            config.PIDFILE.unlink(missing_ok=True)
    return 0


def _tail_text(path, lines: int = 10) -> str:
    try:
        content = path.read_text(errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(content[-lines:])


def open_playground(host: str, port: int) -> None:
    """Open the playground in the default browser; a machine without one just keeps the printed URL."""
    browsable_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    webbrowser.open(f"http://{browsable_host}:{port}/")


def up(host: str, port: int, open_browser: bool = False) -> int:
    """Start the gateway in the background and wait until it answers, then open the playground if asked."""
    refusal = _non_loopback_refusal(host)
    if refusal:
        print(refusal, file=sys.stderr)
        return 1
    if gateway_pid():
        record = _read_record()
        running_host = record.get("host", host)
        running_port = record.get("port", port)
        print(f"omni already running: http://{running_host}:{running_port}/  (pid {record.get('pid')})")
        if (running_host, running_port) != (host, port):
            print(f"omni: that instance is not on {host}:{port}; `omni down` first to start there",
                  file=sys.stderr)
            return 1
        if open_browser:
            open_playground(running_host, running_port)
        return 0
    config.ensure_dirs()
    log_path = config.LOG_DIR / "omni.log"
    with log_path.open("ab") as logfile:
        process = subprocess.Popen(
            [sys.executable, "-m", "omni.cli", "serve", "--host", host, "--port", str(port)],
            stdin=subprocess.DEVNULL, stdout=logfile, stderr=subprocess.STDOUT, start_new_session=True,
        )
    deadline = time.monotonic() + UP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            print(f"omni: gateway exited with {process.returncode}; see {log_path}", file=sys.stderr)
            tail = _tail_text(log_path)
            if tail:
                print(tail, file=sys.stderr)
            return 1
        try:
            response = httpx.get(f"http://{host}:{port}/health", timeout=1.0)
            if response.status_code != 200:
                raise httpx.HTTPError("not ready")
        except httpx.HTTPError:
            time.sleep(POLL_SECONDS)
            continue
        print(f"omni up: http://{host}:{port}/  "
              f"(pid {_read_record().get('pid')}, logs {log_path}; workers warm up in the background)")
        if open_browser:
            open_playground(host, port)
        return 0
    process.terminate()
    print(f"omni: gateway did not answer in {UP_TIMEOUT_SECONDS}s; stopped it; see {log_path}", file=sys.stderr)
    return 1


def stop_gateway() -> bool:
    """Stop the gateway and the workers it left behind; whether one was running."""
    pid = gateway_pid()
    if pid:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        if not _wait_gone(pid, DOWN_TIMEOUT_SECONDS):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        config.PIDFILE.unlink(missing_ok=True)
    config.ensure_dirs()
    leftovers = supervisor.kill_leftovers()
    if leftovers:
        print(f"stopped leftover workers: {', '.join(leftovers)}")
    return pid is not None


def down() -> int:
    """Stop the gateway and every worker it left behind. Stopping what is not running is a success."""
    print("omni down" if stop_gateway() else "omni was not running")
    return 0


def restart(host: str, port: int) -> int:
    """Stop the gateway and start it again on the same address, so it rereads the config."""
    if not gateway_pid():
        print("omni is not running; `omni up` to start it", file=sys.stderr)
        return 1
    stop_gateway()
    return up(host, port)


def status(gateway: dict, json_output: bool = False) -> int:
    """Print the gateway's health report, as a table or as JSON, or say it is not running."""
    host, port = address(gateway)
    try:
        health = httpx.get(f"http://{host}:{port}/health", timeout=2.0).json()
    except (httpx.HTTPError, ValueError):
        print("omni is not running")
        return 1
    if json_output:
        print(json.dumps(health, indent=2))
        return 0
    print(_health_table(health, f"http://{host}:{port}/"))
    return 0


def _health_table(health: dict, url: str) -> str:
    """The health report as one header line and a fixed-width row per worker."""
    pid = _read_record().get("pid")
    rows = [("role", "state", "model", "rss", "cpu")]
    for role, entry in health.get("workers", {}).items():
        rows.append((role, entry.get("status", ""), entry.get("model") or "-",
                     _mebibytes(entry.get("rss_bytes")), _percent(entry.get("cpu_percent"))))
    widths = [max(len(row[column]) for row in rows) for column in range(len(rows[0]))]
    lines = [f"omni {version() or 'unknown'}  pid {pid or '-'}  {url}  up {_uptime(pid)}"]
    lines.extend("  ".join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip() for row in rows)
    pins = health.get("pins") or []
    if pins:
        lines.append(f"pinned: {', '.join(pins)}")
    return "\n".join(lines)


def _uptime(pid) -> str:
    if not pid:
        return "-"
    try:
        seconds = int(time.time() - psutil.Process(pid).create_time())
    except psutil.Error:
        return "-"
    if seconds >= 86400:
        return f"{seconds // 86400}d {seconds % 86400 // 3600}h"
    if seconds >= 3600:
        return f"{seconds // 3600}h {seconds % 3600 // 60}m"
    if seconds >= 60:
        return f"{seconds // 60}m"
    return f"{seconds}s"


def _mebibytes(value) -> str:
    """A resident size for the table; a role omni does not own has none to report."""
    return f"{value / 1024 / 1024:.0f} MB" if value else "-"


def _percent(value) -> str:
    return f"{value:.0f}%" if value else "-"


def print_logs(role: str | None = None, lines: int = 200, follow: bool = False) -> int:
    """Print the gateway's log, or one worker's, following it when asked."""
    path = logs.path_for(role)
    if not path.exists():
        print(f"omni: no log at {path}" + (" (omni has not started yet)" if role is None else ""),
              file=sys.stderr)
        return 1
    for line in logs.tail(path, lines):
        print(line)
    return _follow(path) if follow else 0


def _follow(path) -> int:
    """Print what is appended to `path` until interrupted."""
    try:
        with path.open("rb") as file:
            file.seek(0, 2)
            while True:
                chunk = file.readline()
                if not chunk:
                    time.sleep(POLL_SECONDS)
                    continue
                print(chunk.decode(errors="replace").rstrip(), flush=True)
    except KeyboardInterrupt:
        return 0
