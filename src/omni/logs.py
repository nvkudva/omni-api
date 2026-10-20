"""Where each role's log lives, and the last lines of one.

The gateway, the CLI and the admin routes all read the same files: `omni.log` holds the gateway and
every worker it spawned, while the roles Ollama serves write to Ollama's own server log.
"""
from pathlib import Path

from omni import config, registry

# enough to see a stack trace without loading a multi-gigabyte log into memory
TAIL_BYTES = 256 * 1024

def path_for(role: str | None) -> Path:
    """The log a role writes; None is the gateway's own log."""
    if role is None:
        return config.LOG_DIR / "omni.log"
    if role in registry.OLLAMA_ROLES:
        # read `home` here, not at import, so a changed HOME is honoured
        return Path.home() / ".ollama" / "logs" / "server.log"
    return config.LOG_DIR / f"{role}.log"


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
