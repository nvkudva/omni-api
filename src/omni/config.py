import json
import os
import tomllib
from pathlib import Path
from urllib.parse import urlsplit

HOME = Path(os.environ.get("OMNI_HOME", Path.home() / ".omni-api"))
RUN_DIR = HOME / "run"
LOG_DIR = HOME / "logs"
VIDEO_DIR = HOME / "videos"
PIDFILE = RUN_DIR / "omni.pid"
USER_CONFIG = HOME / "config.toml"
CATALOG = Path(__file__).with_name("catalog.toml")
OLLAMA_DEFAULT_PORT = 11434


def load() -> dict:
    """The shipped catalog, with any section values from ~/.omni-api/config.toml layered on top."""
    with CATALOG.open("rb") as file:
        settings = tomllib.load(file)
    if USER_CONFIG.exists():
        with USER_CONFIG.open("rb") as file:
            overrides = tomllib.load(file)
        for section, values in overrides.items():
            settings.setdefault(section, {}).update(values)
    return settings


def save_override(section: str, key: str, value: str) -> None:
    """Set one value in ~/.omni-api/config.toml, keeping every other override there."""
    overrides = {}
    if USER_CONFIG.exists():
        with USER_CONFIG.open("rb") as file:
            overrides = tomllib.load(file)
    overrides.setdefault(section, {})[key] = value
    lines = []
    for name, values in overrides.items():
        lines.append(f"[{name}]")
        for option, setting in values.items():
            lines.append(f"{option} = {json.dumps(setting)}")
        lines.append("")
    USER_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    USER_CONFIG.write_text("\n".join(lines))


def ollama_url() -> str:
    """Where this machine's Ollama listens: OLLAMA_HOST as Ollama itself reads it, else 127.0.0.1:11434."""
    raw = os.environ.get("OLLAMA_HOST", "").strip()
    parts = urlsplit(raw if "://" in raw else f"http://{raw}")
    host = parts.hostname or "127.0.0.1"
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"
    if ":" in host:
        host = f"[{host}]"
    default_port = 443 if parts.scheme == "https" else OLLAMA_DEFAULT_PORT
    return f"{parts.scheme}://{host}:{parts.port or default_port}"


def ensure_dirs() -> None:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)


def socket_path(role: str) -> Path:
    return RUN_DIR / f"{role}.sock"


def idle_seconds(settings: dict) -> float:
    return float(settings["gateway"]["idle_unload_minutes"]) * 60
