import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time

import httpx

from omni import config, supervisor

UP_TIMEOUT_SECONDS = 30
DOWN_TIMEOUT_SECONDS = 20


def _port_in_use(host: str, port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.5)
        return probe.connect_ex((host, port)) == 0


def _gateway_pid() -> int | None:
    try:
        pid = int(config.PIDFILE.read_text())
        os.kill(pid, 0)
        return pid
    except (FileNotFoundError, ValueError, ProcessLookupError):
        return None


def serve(host: str, port: int) -> int:
    import uvicorn

    from omni import gateway

    if _port_in_use(host, port):
        print(f"omni: {host}:{port} is already in use (another omni?); `omni status` to check", file=sys.stderr)
        return 1
    config.ensure_dirs()
    config.PIDFILE.write_text(str(os.getpid()))
    try:
        uvicorn.run(gateway.create(), host=host, port=port, log_level="info")
    finally:
        config.PIDFILE.unlink(missing_ok=True)
    return 0


def up(host: str, port: int) -> int:
    if _gateway_pid():
        print(f"omni already running: http://{host}:{port}/playground")
        return 0
    config.ensure_dirs()
    logfile = (config.LOG_DIR / "omni.log").open("ab")
    subprocess.Popen(
        [sys.executable, "-m", "omni.cli", "serve", "--host", host, "--port", str(port)],
        stdin=subprocess.DEVNULL, stdout=logfile, stderr=subprocess.STDOUT, start_new_session=True,
    )
    deadline = time.monotonic() + UP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        try:
            httpx.get(f"http://{host}:{port}/health", timeout=1.0)
            print(f"omni up: http://{host}:{port}/playground  (workers warm up in the background; `omni status`)")
            return 0
        except httpx.HTTPError:
            time.sleep(0.5)
    print(f"omni: gateway did not answer in {UP_TIMEOUT_SECONDS}s; see {config.LOG_DIR / 'omni.log'}", file=sys.stderr)
    return 1


def down() -> int:
    pid = _gateway_pid()
    if pid:
        os.kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + DOWN_TIMEOUT_SECONDS
        while time.monotonic() < deadline and _gateway_pid():
            time.sleep(0.2)
        if _gateway_pid():
            os.kill(pid, signal.SIGKILL)
        config.PIDFILE.unlink(missing_ok=True)
    config.ensure_dirs()
    leftovers = supervisor.kill_leftovers()
    if leftovers:
        print(f"stopped leftover workers: {', '.join(leftovers)}")
    print("omni down" if pid else "omni was not running")
    return 0


def status(host: str, port: int) -> int:
    try:
        health = httpx.get(f"http://{host}:{port}/health", timeout=2.0).json()
    except httpx.HTTPError:
        print("omni is not running")
        return 1
    print(json.dumps(health, indent=2))
    return 0


def _install_ollama() -> int:
    if shutil.which("brew"):
        argv = ["brew", "install", "ollama"]
    else:
        argv = ["sh", "-c", "curl -fsSL https://ollama.com/install.sh | sh"]
    print(f"ollama not found; installing: {' '.join(argv)}")
    return subprocess.run(argv).returncode


def init() -> int:
    if shutil.which("ollama"):
        print(f"ollama installed: {shutil.which('ollama')}")
    elif _install_ollama() != 0 or not shutil.which("ollama"):
        print("ollama install failed; see https://ollama.com/download", file=sys.stderr)
        return 1
    ollama_url = config.ollama_url()
    try:
        httpx.get(f"{ollama_url}/api/version", timeout=2.0).raise_for_status()
    except httpx.HTTPError:
        print(f"ollama is not running at {ollama_url}; models are pulled on the next `omni init` once it is")
        return 0
    settings = config.load()
    models = {settings[role]["model"] for role in ("chat", "decide", "embed")}
    for model in sorted(models):
        print(f"pulling {model}")
        if subprocess.run(["ollama", "pull", model]).returncode != 0:
            return 1
    return 0


def main() -> None:
    settings = config.load()["gateway"]
    parser = argparse.ArgumentParser(prog="omni", description="local models behind one gateway port")
    parser.add_argument("--host", default=settings["host"])
    parser.add_argument("--port", type=int, default=settings["port"])
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help_text in [
        ("init", "install ollama if missing and pull the chat, decide and embed models"),
        ("serve", "run the gateway and its workers in the foreground"),
        ("up", "start omni in the background"),
        ("down", "stop omni and every worker"),
        ("status", "show worker health"),
    ]:
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--host", default=argparse.SUPPRESS)
        command.add_argument("--port", type=int, default=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.command == "init":
        sys.exit(init())
    if args.command == "serve":
        sys.exit(serve(args.host, args.port))
    if args.command == "up":
        sys.exit(up(args.host, args.port))
    if args.command == "down":
        sys.exit(down())
    sys.exit(status(args.host, args.port))


if __name__ == "__main__":
    main()
