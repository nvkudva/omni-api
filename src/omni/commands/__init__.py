"""The ``omni`` command line: install Ollama, then run and stop the gateway.

``main`` is the entry point wired in ``pyproject.toml``. The work lives in ``lifecycle`` and ``init``;
this module only parses arguments and dispatches.
"""
import argparse
import sys
from importlib.metadata import PackageNotFoundError, version

from omni import config
from omni.commands import init, lifecycle

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 10000

COMMANDS = (
    ("init", "install ollama if missing and pull the chat, decide and embed models"),
    ("serve", "run the gateway and its workers in the foreground"),
    ("up", "start omni in the background"),
    ("down", "stop omni and every worker"),
    ("status", "show worker health"),
)


def _version() -> str:
    try:
        return version("omni-api")
    except PackageNotFoundError:
        return "0.0.0+unknown"


def _gateway_defaults() -> dict:
    try:
        return config.load()["gateway"]
    except config.ConfigError as error:
        print(f"omni: {error}", file=sys.stderr)
        return {"host": DEFAULT_HOST, "port": DEFAULT_PORT}


def build_parser() -> argparse.ArgumentParser:
    settings = _gateway_defaults()
    parser = argparse.ArgumentParser(prog="omni", description="local models behind one gateway port")
    parser.add_argument("--version", action="version", version=f"omni {_version()}")
    parser.add_argument("--host", default=settings["host"])
    parser.add_argument("--port", type=int, default=settings["port"])
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help_text in COMMANDS:
        command = commands.add_parser(name, help=help_text)
        # SUPPRESS keeps the top-level --host/--port when a subcommand does not pass its own
        command.add_argument("--host", default=argparse.SUPPRESS)
        command.add_argument("--port", type=int, default=argparse.SUPPRESS)
    commands.choices["init"].add_argument(
        "--small", action="store_true", help="make the smallest model of each role the default")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    host, port = args.host, args.port
    if args.command == "init":
        sys.exit(init.run(small=args.small))
    if args.command == "serve":
        sys.exit(lifecycle.serve(host, port))
    if args.command == "up":
        sys.exit(lifecycle.up(host, port))
    if args.command == "down":
        sys.exit(lifecycle.down())
    sys.exit(lifecycle.status(host, port))
