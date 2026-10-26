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
BUNDLE_DIR = HOME / "bundles"
PIDFILE = RUN_DIR / "omni.pid"
PINS = HOME / "pins.json"
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
    "video": {"model": str, "fps": int},
}

# Keys a section may carry without needing them. `image.file` (and `video.file`) names a GGUF
# transformer inside the role's model, and `base` is the checkpoint that supplies the text
# encoder and VAE for it.
_OPTIONAL: dict[str, dict[str, type]] = {
    "image": {"base": str, "file": str},
    "video": {"base": str, "file": str, "steps": int},
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
        extra = set(values) - set(keys) - set(_OPTIONAL.get(section, {}))
        if extra:
            raise ConfigError(f"[{section}] has unknown keys: {', '.join(sorted(extra))}")
        for key, kind in _OPTIONAL.get(section, {}).items():
            if key in values and not _is_type(values[key], kind):
                raise ConfigError(f"{section}.{key} must be {kind.__name__}")
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
    if not 0 <= gateway["idle_unload_minutes"] <= 1440:
        raise ConfigError("gateway.idle_unload_minutes must be between 0 (never unload) and 1440")
    if not gateway["host"].strip():
        raise ConfigError("gateway.host must not be empty")
    for section in ROLES:
        if not settings[section]["model"].strip():
            raise ConfigError(f"{section}.model must not be empty")
    if not 1 <= settings["image"]["steps"] <= 100:
        raise ConfigError("image.steps must be between 1 and 100")
    if not 1 <= settings["video"]["fps"] <= 60:
        raise ConfigError("video.fps must be between 1 and 60")


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
    if not _known(section, key):
        raise ConfigError(f"unknown setting {section}.{key}")
    overrides = _read(USER_CONFIG) if USER_CONFIG.exists() else {}
    overrides.setdefault(section, {})[key] = value
    lines = []
    for name, values in overrides.items():
        lines.append(f"[{name}]")
        lines.extend(f"{option} = {toml_value(setting)}" for option, setting in values.items())
        lines.append("")
    USER_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    temporary = USER_CONFIG.with_name(f".{USER_CONFIG.name}.tmp")
    temporary.write_text("\n".join(lines))
    os.replace(temporary, USER_CONFIG)


def _known(section: str, key: str) -> bool:
    """Whether `section.key` is a setting the schema allows."""
    return section in _SCHEMA and (key in _SCHEMA[section] or key in _OPTIONAL.get(section, {}))


def set_setting(section: str, key: str, raw: str):
    """Store one setting from its command-line text, and return the value that was stored.

    The whole file is validated before it is written, so `omni config set` cannot leave a config
    behind that `omni up` would refuse to start with.
    """
    if section not in _SCHEMA:
        raise ConfigError(f"unknown section [{section}]")
    if not _known(section, key):
        raise ConfigError(f"unknown setting {section}.{key}")
    kind = _SCHEMA[section].get(key) or _OPTIONAL.get(section, {}).get(key)
    value = _coerce(raw, kind, f"{section}.{key}")
    settings = load()
    settings[section][key] = value
    validate(settings)
    save_override(section, key, value)
    return value


def _coerce(raw: str, kind: type, name: str):
    """`raw` as the type the schema expects, or a ConfigError naming the setting."""
    if kind is str:
        return raw
    try:
        return float(raw) if kind is float else int(raw)
    except ValueError as error:
        raise ConfigError(f"{name} must be {kind.__name__}, got {raw!r}") from error


def dump(settings: dict) -> str:
    """The effective settings as TOML, in schema order, so the output can be saved as a config."""
    lines = []
    for section in _SCHEMA:
        lines.append(f"[{section}]")
        lines.extend(f"{key} = {toml_value(value)}" for key, value in settings[section].items())
        lines.append("")
    return "\n".join(lines)


def toml_value(value) -> str:
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
    try:
        port = parts.port
    except ValueError as error:
        raise ConfigError(f"OLLAMA_HOST has an invalid port: {raw!r}") from error
    default_port = 443 if parts.scheme == "https" else OLLAMA_DEFAULT_PORT
    return f"{parts.scheme}://{host}:{port or default_port}"


LOG_MAX_BYTES = 10 * 1024 * 1024
LOG_KEEP_BYTES = 1024 * 1024


def trim_log(path: Path) -> None:
    """Cut a log past LOG_MAX_BYTES down to its newest LOG_KEEP_BYTES, on a line boundary.

    Only for a log nothing holds open: call it just before a process that appends to it starts.
    """
    try:
        if path.stat().st_size <= LOG_MAX_BYTES:
            return
        with path.open("rb") as file:
            file.seek(-LOG_KEEP_BYTES, os.SEEK_END)
            tail = file.read()
        tail = tail[tail.find(b"\n") + 1:]
        temporary = path.with_name(f".{path.name}.tmp")
        temporary.write_bytes(tail)
        os.replace(temporary, path)
    except OSError:
        pass


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


def load_pins() -> list[str]:
    """The roles whose models should stay resident, in role order. Unknown names are dropped."""
    try:
        raw = json.loads(PINS.read_text())
    except (OSError, ValueError):
        return []
    names = raw.get("pinned") if isinstance(raw, dict) else raw
    if not isinstance(names, list):
        return []
    return [role for role in ROLES if role in names]


def save_pins(names) -> list[str]:
    """Store the pinned roles, dropping anything that is not a role. Returns what was stored.

    Written to a sibling temp file and renamed, so a crash cannot truncate the file.
    """
    pinned = [role for role in ROLES if role in set(names)]
    try:
        PINS.parent.mkdir(parents=True, exist_ok=True)
        temporary = PINS.with_name(f".{PINS.name}.tmp")
        temporary.write_text(json.dumps({"pinned": pinned}, indent=2))
        os.replace(temporary, PINS)
    except OSError:
        return load_pins()
    return pinned
