"""The ``omni`` command line: install Ollama, then run, inspect and stop the gateway.

``main`` is the entry point wired in ``pyproject.toml``. The work lives in ``doctor``, ``init``,
``lifecycle`` and ``settings``; this module only parses arguments and dispatches.
"""
import argparse
import sys

from omni import config, logs, version
from omni.commands import doctor, init, lifecycle, settings

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 10000

COMMANDS = (
    ("init", "ask before installing ollama, then pull the chat, decide and embed models and load stt and tts"),
    ("doctor", "report what works and what to fix, without changing anything"),
    ("serve", "run the gateway and its workers in the foreground"),
    ("up", "start omni in the background"),
    ("restart", "restart a running omni on the address it already bound"),
    ("down", "stop omni and every worker"),
    ("status", "show worker health as a table, or as JSON"),
    ("logs", "print the gateway log, or one worker's"),
    ("config", "show, check or change ~/.omni-api/config.toml"),
)

# what `omni` on its own prints: the same commands as --help, but grouped by what you are doing
GUIDE = """\
omni - local models behind one gateway port

getting started
  omni init                  ask before installing ollama, pull chat/decide/embed, download and start stt/tts
  omni init --media          also load the image and video models now
  omni doctor                check the install read-only and say what to fix
  omni config                show the effective settings
  omni config set SECTION.KEY VALUE
  omni config check          validate the config file without starting anything

running
  omni up                    start in the background and open the playground; --no-browser skips that
  omni up --foreground       run in this terminal instead
  omni serve                 the same as `omni up --foreground`
  omni restart               stop and start again, rereading the config
  omni down                  stop the gateway and every worker

watching
  omni status                worker health as a table; --json for the raw report
  omni logs                  the gateway log; `omni logs stt` one worker's, -f follows

  --host/--port belong to serve and up alone; the other commands use the address the
  gateway recorded. `omni <command> --help` lists that command's flags.
"""


def _gateway_defaults() -> dict:
    try:
        return config.load()["gateway"]
    except config.ConfigError as error:
        print(f"omni: {error}", file=sys.stderr)
        return {"host": DEFAULT_HOST, "port": DEFAULT_PORT}


def _add_gateway_address(command: argparse.ArgumentParser, gateway: dict) -> None:
    command.add_argument("--host", default=gateway["host"], help=f"default {gateway['host']}")
    command.add_argument("--port", type=int, default=gateway["port"], help=f"default {gateway['port']}")


def _add_config_actions(command: argparse.ArgumentParser) -> None:
    command.set_defaults(action="show")
    actions = command.add_subparsers(dest="action")
    actions.add_parser("show", help="print the effective settings as TOML")
    actions.add_parser("check", help="fail when the config does not load")
    setter = actions.add_parser("set", help="change one value, e.g. gateway.port 10001")
    setter.add_argument("target", metavar="SECTION.KEY")
    setter.add_argument("value")


def build_parser(gateway: dict | None = None) -> argparse.ArgumentParser:
    gateway = _gateway_defaults() if gateway is None else gateway
    parser = argparse.ArgumentParser(prog="omni", description="local models behind one gateway port")
    parser.add_argument("--version", action="version", version=f"omni {version() or 'unknown'}")
    commands = parser.add_subparsers(dest="command")
    for name, help_text in COMMANDS:
        command = commands.add_parser(name, help=help_text)
        if name in ("serve", "up"):
            _add_gateway_address(command, gateway)
        if name == "up":
            command.add_argument("--foreground", action="store_true",
                                 help="run in this terminal instead of the background")
            command.add_argument("--no-browser", action="store_true",
                                 help="do not open the playground in the browser")
        if name == "init":
            command.add_argument("--media", action="store_true",
                                 help="also load the image and video models now (stt and tts always load)")
        if name == "status":
            command.add_argument("--json", action="store_true", help="print the raw health report")
        if name == "logs":
            command.add_argument("role", nargs="?", choices=(*config.ROLES, logs.OLLAMA_ENGINE),
                                 help="a role's log instead of the gateway's; 'ollama' is the engine's")
            command.add_argument("--lines", type=int, default=200, help="default 200")
            command.add_argument("-f", "--follow", action="store_true", help="keep printing as it grows")
        if name == "config":
            _add_config_actions(command)
    return parser


def _enable_completion(parser: argparse.ArgumentParser) -> None:
    """Tab completion, when the optional `completion` extra is installed."""
    try:
        import argcomplete
    except ImportError:
        return
    argcomplete.autocomplete(parser)


def main() -> None:
    gateway = _gateway_defaults()
    parser = build_parser(gateway)
    _enable_completion(parser)
    args = parser.parse_args()
    if args.command is None:
        print(GUIDE, end="")
        sys.exit(0)
    if args.command == "init":
        sys.exit(init.run(all_media=args.media))
    if args.command == "doctor":
        sys.exit(doctor.run())
    if args.command == "serve":
        sys.exit(lifecycle.serve(args.host, args.port))
    if args.command == "up":
        sys.exit(lifecycle.serve(args.host, args.port) if args.foreground else lifecycle.up(args.host, args.port, open_browser=not args.no_browser))
    if args.command == "restart":
        sys.exit(lifecycle.restart(*lifecycle.address(gateway)))
    if args.command == "down":
        sys.exit(lifecycle.down())
    if args.command == "status":
        sys.exit(lifecycle.status(gateway, json_output=args.json))
    if args.command == "logs":
        sys.exit(lifecycle.print_logs(args.role, args.lines, args.follow))
    sys.exit(settings.run(args.action, getattr(args, "target", None), getattr(args, "value", None)))
