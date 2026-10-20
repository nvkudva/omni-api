"""``omni config``: show, check or change the settings in ~/.omni-api/config.toml."""
from omni import config
from omni.commands import init


def run(action: str, target: str | None = None, value: str | None = None) -> int:
    """Dispatch `show`, `check` and `set`; `set` gets the SECTION.KEY and its new text."""
    if action == "check":
        return check()
    if action == "set":
        return set_value(target or "", value or "")
    return show()


def show() -> int:
    """Print the effective settings as TOML, so the output can be pasted into a config file."""
    try:
        settings = config.load()
    except config.ConfigError as failure:
        return init.error(f"omni: bad configuration: {failure}",
                          f"  fix {config.USER_CONFIG}, then `omni config` again",
                          f"  the shipped defaults are {config.CATALOG}")
    note = "" if config.USER_CONFIG.exists() else "  (not created yet; these are the shipped defaults)"
    print(f"# {config.USER_CONFIG}{note}")
    print(config.dump(settings), end="")
    return 0


def check() -> int:
    """Validate the config and say where it came from, without touching the gateway."""
    try:
        config.load()
    except config.ConfigError as failure:
        return init.error(f"omni: bad configuration: {failure}",
                          f"  fix {config.USER_CONFIG}, then `omni config check` again")
    source = config.USER_CONFIG if config.USER_CONFIG.exists() else config.CATALOG
    print(f"config ok: {source}")
    return 0


def set_value(target: str, value: str) -> int:
    """Store one setting, refusing anything the schema or `validate` rejects."""
    section, _, key = target.partition(".")
    if not key:
        return init.error(f"omni: expected SECTION.KEY, got {target!r}",
                          "  e.g. omni config set gateway.port 10001")
    try:
        stored = config.set_setting(section, key, value)
    except config.ConfigError as failure:
        return init.error(f"omni: {failure}")
    print(f"{section}.{key} = {config.toml_value(stored)}")
    print("applies on the next start: `omni restart`")
    return 0
