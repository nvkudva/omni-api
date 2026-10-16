"""Paths, the shipped catalog, the user's override file, and where Ollama listens.

``load()`` merges ``catalog.toml`` with ``~/.omni-api/config.toml`` and validates the result, so a
typo in a section name or an out-of-range port fails loudly at start-up instead of at first request.
"""
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

# Registration order is also the order roles are listed in the playground.
ROLES = ("chat", "decide", "embed", "stt", "tts", "image", "video")

# section -> required keys and expected type
_SCHEMA: dict[str, dict[str, type]] = {
    "gateway": {"host": str, "port": int, "idle_unload_minutes": float},
    "chat": {"model": str},
    "decide": {"model": str},
    "embed": {"model": str},
    "stt": {"model": str},
    "tts": {"model": str, "voice": str},
    "image": {"model": str, "steps": int},
    "video": {"model": str, "frames": int, "fps": int},
}


class ConfigError(ValueError):
    """The catalog or the user's config.toml is unusable."""


def load() -> dict:
    """The shipped catalog, with any section values from ~/.omni-api/config.toml layered on top."""
    settings = _read(CATALOG)
    if USER_CONFIG.exists():
        _merge(settings, _read(USER_CONFIG))
    validate(settings)
    return settings


def _is_type(value, kind: type) -> bool:
    """Whether a config value has the expected type. Rejects bool everywhere (it subclasses int),
    and accepts a TOML integer where a float is expected, e.g. ``idle_unload_minutes = 10``."""
    if isinstance(value, bool):
        return False
    if kind is float:
        return isinstance(value, (int, float))
    return isinstance(value, kind)


def validate(settings: dict) -> None:
    """Every role has a model and the gateway numbers are usable; raises ConfigError otherwise."""
    for section, keys in _SCHEMA.items():
        values = settings.get(section)
        if not isinstance(values, dict):
            raise ConfigError(f"[{section}] is missing from {CATALOG.name}")
        extra = set(values) - set(keys)
        if extra:
            raise ConfigError(f"[{section}] has unknown keys: {', '.join(sorted(extra))}")
        for key, kind in keys.items():
            if key not in values:
                raise ConfigError(f"[{section}] is missing {key}")
            value = values[key]
            # bool is a subclass of int: never accept it where a number is expected.
            if not _is_type(value, kind):
                raise ConfigError(f"{section}.{key} must be {kind.__name__}")
    gateway = settings["gateway"]
    if not 1 <= gateway["port"] <= 65535:
        raise ConfigError("gateway.port must be between 1 and 65535")
    if gateway["idle_unload_minutes"] <= 0:
        raise ConfigError("gateway.idle_unload_minutes must be greater than 0")
    if settings["image"]["steps"] < 1:
        raise ConfigError("image.steps must be at least 1")
    if settings["video"]["frames"] < 1 or settings["video"]["fps"] <= 0:
        raise ConfigError("video.frames must be at least 1 and video.fps greater than 0")


def _read(path: Path) -> dict:
    try:
        with path.open("rb") as file:
            return tomllib.load(file)
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"{path} is not valid TOML: {error}") from error
    except OSError as error:
        raise ConfigError(f"cannot read {path}: {error}") from error


def _merge(settings: dict, overrides: dict) -> None:
    for section, values in overrides.items():
        if section not in _SCHEMA:
            raise ConfigError(f"unknown section [{section}] in {USER_CONFIG.name}")
        if not isinstance(values, dict):
            raise ConfigError(f"[{section}] in {USER_CONFIG.name} must be a table")
        settings.setdefault(section, {}).update(values)


def save_override(section: str, key: str, value) -> None:
    """Set one value in ~/.omni-api/config.toml, keeping every other override there.

    Written to a sibling temp file and renamed, so a crash cannot truncate the file.
    """
    if section not in _SCHEMA or key not in _SCHEMA[section]:
        raise ConfigError(f"unknown setting {section}.{key}")
    overrides = _read(USER_CONFIG) if USER_CONFIG.exists() else {}
    overrides.setdefault(section, {})[key] = value
    lines = []
    for name, values in overrides.items():
        lines.append(f"[{name}]")
        lines.extend(f"{option} = {_toml_value(setting)}" for option, setting in values.items())
        lines.append("")
    USER_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    temporary = USER_CONFIG.with_name(f".{USER_CONFIG.name}.tmp")
    temporary.write_text("\n".join(lines))
    os.replace(temporary, USER_CONFIG)


def _toml_value(value) -> str:
    if value is None:
        raise ConfigError("cannot store a null value")
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    return json.dumps(value)


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
    """The stable path every client connects to. It is a symlink to the current worker generation.

    Clients keep one path forever, while each worker generation binds its own file, so a worker that
    is slow to exit can only ever unlink the socket it bound itself.
    """
    return RUN_DIR / f"{role}.sock"


def socket_target(role: str, token: str) -> Path:
    """The real socket one worker generation binds. `token` must be unique per spawn."""
    return RUN_DIR / f"{role}.{token}.sock"


def idle_seconds(settings: dict) -> float:
    return float(settings["gateway"]["idle_unload_minutes"]) * 60
