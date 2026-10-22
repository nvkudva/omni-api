"""``omni doctor``: say what works and what to fix, without changing anything.

`omni init` installs and downloads; this only reports. Every line is one check, so the output can be
pasted into a bug report and read at a glance.
"""
import shutil
import sys

import httpx

from omni import config, registry
from omni.commands import init, lifecycle

OK, WARN, FAIL = "ok", "warn", "fail"
MINIMUM_FREE_GIGABYTES = 5

Finding = tuple[str, str, str]


def run() -> int:
    """Print one line per check; non-zero when anything marked fail was found."""
    settings = _settings()
    findings = [
        _check_config(),
        _check_ollama_binary(),
        _check_ollama_server(),
        _check_models(settings),
        _check_gateway(settings),
        _check_disk(),
    ]
    for level, name, detail in findings:
        print(f"{level:<5} {name:<8} {detail}")
    failed = sum(1 for level, _, _ in findings if level == FAIL)
    if failed:
        print(f"{failed} problem{'s' if failed > 1 else ''}; fix the lines marked {FAIL}", file=sys.stderr)
        return 1
    print("no problems found")
    return 0


def _settings() -> dict | None:
    try:
        return config.load()
    except config.ConfigError:
        return None


def _check_config() -> Finding:
    try:
        config.load()
    except config.ConfigError as failure:
        return FAIL, "config", f"{failure}; fix {config.USER_CONFIG}"
    path = config.USER_CONFIG if config.USER_CONFIG.exists() else config.CATALOG
    return OK, "config", str(path)


def _check_ollama_binary() -> Finding:
    found = shutil.which("ollama")
    if found:
        return OK, "ollama", found
    return FAIL, "ollama", f"not on PATH; `omni init` installs it ({init.OLLAMA_DOWNLOAD_URL})"


def _check_ollama_server() -> Finding:
    try:
        url = config.ollama_url()
    except config.ConfigError as failure:
        return FAIL, "server", str(failure)
    try:
        answer = httpx.get(f"{url}/api/version", timeout=2.0).json()
    except (httpx.HTTPError, ValueError):
        return FAIL, "server", f"not answering at {url}; `ollama serve`, then `omni init`"
    reported = answer.get("version", "") if isinstance(answer, dict) else ""
    return OK, "server", f"{url} (version {reported})" if reported else url


def _check_models(settings: dict | None) -> Finding:
    if settings is None:
        return WARN, "models", "skipped: the config does not load"
    try:
        url = config.ollama_url()
    except config.ConfigError:
        return WARN, "models", "skipped: OLLAMA_HOST is unusable"
    present = init.installed(url)
    if present is None:
        return WARN, "models", f"skipped: ollama is not answering at {url}"
    have = {name.removesuffix(":latest") for name in present}
    wanted = sorted({settings[role]["model"] for role in registry.OLLAMA_ROLES})
    missing = [model for model in wanted if model.removesuffix(":latest") not in have]
    if missing:
        return FAIL, "models", f"missing {', '.join(missing)}; `omni init`"
    return OK, "models", f"{', '.join(wanted)} present"


def _check_gateway(settings: dict | None) -> Finding:
    if settings is None:
        return WARN, "gateway", "skipped: the config does not load"
    host, port = lifecycle.address(settings["gateway"])
    pid = lifecycle.gateway_pid()
    if not pid:
        if lifecycle.port_in_use(host, port):
            return FAIL, "gateway", f"{host}:{port} is in use by something that is not omni"
        return WARN, "gateway", f"not running on {host}:{port}; `omni up`"
    try:
        health = httpx.get(f"http://{host}:{port}/health", timeout=2.0).json()
    except (httpx.HTTPError, ValueError):
        return FAIL, "gateway", f"pid {pid} but /health does not answer on {host}:{port}"
    if health.get("ok"):
        return OK, "gateway", f"pid {pid} on {host}:{port}"
    return WARN, "gateway", f"pid {pid} but workers are not all ready; `omni status`"


def _check_disk() -> Finding:
    path = config.HOME if config.HOME.exists() else config.HOME.parent
    try:
        free = shutil.disk_usage(path).free / 1024**3
    except OSError as failure:
        return FAIL, "disk", f"{path}: {failure}"
    level = OK if free >= MINIMUM_FREE_GIGABYTES else WARN
    return level, "disk", f"{free:.0f} GB free at {path}"
