"""Where each role's log lives, and the last lines of one.

The gateway, the CLI and the admin routes all read the same files: `omni.log` holds the gateway and
every worker it spawned, while one Ollama server answers chat, decide and embed and writes the
engine's log those three share.
"""
from datetime import datetime
from pathlib import Path

from omni import config, registry

# enough to see a stack trace without loading a multi-gigabyte log into memory
TAIL_BYTES = 256 * 1024

# the name the shared engine log answers to; it belongs to no single role
OLLAMA_ENGINE = "ollama"


def path_for(role: str | None) -> Path:
    """The log a role writes; None is the gateway's own log."""
    if role is None:
        return config.LOG_DIR / "omni.log"
    if role == OLLAMA_ENGINE:
        # read `home` here, not at import, so a changed HOME is honoured
        return Path.home() / ".ollama" / "logs" / "server.log"
    if role in registry.OLLAMA_ROLES:
        # Ollama writes one log for the whole engine, so omni keeps the role's own:
        # that role's requests and what it did with its model
        return config.LOG_DIR / "requests" / f"{role}.log"
    return config.LOG_DIR / f"{role}.log"


def note(role: str, message: str) -> None:
    """Write one line into a role's own log, so its page shows what that role did."""
    try:
        path = path_for(role)
        path.parent.mkdir(parents=True, exist_ok=True)
        if role in registry.OLLAMA_ROLES:  # a Python worker holds its log open; replacing the file would orphan it
            config.trim_log(path)
        with path.open("a") as file:
            file.write(f"{datetime.now().isoformat(timespec='seconds')} {message}\n")
    except OSError:
        pass


def tail(path: Path, lines: int) -> list[str]:
    """The last `lines` lines of `path`, or nothing when it cannot be read."""
    try:
        with path.open("rb") as file:
            file.seek(0, 2)
            file.seek(max(0, file.tell() - TAIL_BYTES))
            text = file.read().decode(errors="replace")
    except OSError:
        return []
    return text.splitlines()[-lines:]
