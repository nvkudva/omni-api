"""``omni init``: check the setup, install Ollama if it is missing, pull the models it serves, then load decide, stt and tts.

Every step states its own precondition and, when it fails, the one thing to fix — the command to run
and a link — so a fresh machine can go from clone to ``omni up`` without reading the source.
"""
import os
import shutil
import signal
import subprocess
import sys
import threading
import time

import httpx

from omni import config, registry
from omni.registry.memory import GB
from omni.supervisor import guard

# decide and the speech models are small and every install wants them; image and video weigh tens of GB, so `--media` opts in
SMALL_ROLES = ("decide", "stt", "tts")
MEDIA_ROLES = (*SMALL_ROLES, "image", "video")
# a first load reads or downloads weights from Hugging Face, which is slow but not endless
MEDIA_LOAD_TIMEOUT = httpx.Timeout(10.0, read=1800.0)
DOWNLOAD_PROGRESS_SECONDS = 10.0
# how long a freshly started server gets to answer, and how often it is asked
SERVER_START_SECONDS = 20.0
PROBE_SECONDS = 0.5
# the guard gives ollama this long to exit before it kills it, so wait a little longer than that
STOP_SERVER_SECONDS = guard.GRACE_SECONDS + 3.0

OLLAMA_DOWNLOAD_URL = "https://ollama.com/download"
OLLAMA_INSTALL_SCRIPT = "https://ollama.com/install.sh"
OLLAMA_LIBRARY_URL = "https://ollama.com/library"
OLLAMA_TROUBLESHOOTING_URL = "https://github.com/ollama/ollama/blob/main/docs/faq.md"
HUGGINGFACE_URL = "https://huggingface.co"


def error(*lines: str) -> int:
    """Explain a failure on stderr and hand back the exit code the command should use."""
    for line in lines:
        print(line, file=sys.stderr)
    return 1


def install_command() -> list[str]:
    """How this machine would install Ollama, or an empty list when there is no route to one."""
    if shutil.which("brew"):
        return ["brew", "install", "ollama"]
    if shutil.which("curl") and shutil.which("sh"):
        return ["sh", "-c", f"curl -fsSL {OLLAMA_INSTALL_SCRIPT} | sh"]
    return []


def install_ollama() -> bool:
    """Install Ollama with whatever this machine has, or False when there is no route to one."""
    argv = install_command()
    if not argv:
        print("ollama is not installed and there is no brew, curl or sh here to install it with",
              file=sys.stderr)
        return False
    print(f"installing: {' '.join(argv)}", flush=True)
    try:
        return subprocess.run(argv, check=False).returncode == 0
    except OSError as failure:
        print(f"could not run {' '.join(argv)}: {failure}", file=sys.stderr)
        return False


def wants_install() -> bool:
    """Ask before installing Ollama; a terminal that cannot answer keeps the unattended behaviour."""
    argv = install_command()
    if not argv:
        return True  # nothing to ask about: install_ollama explains that there is no route
    command = " ".join(argv)
    if not sys.stdin.isatty():
        print(f"ollama not found; installing it with `{command}`", flush=True)
        return True
    print("ollama is not installed; omni needs it for chat and embed.", file=sys.stderr)
    print(f"  it will run: {command}", file=sys.stderr)
    try:
        answer = input("install ollama now? [y/N] ")
    except (EOFError, KeyboardInterrupt):
        print(file=sys.stderr)
        return False
    return answer.strip().lower() in ("y", "yes")


def require_ollama() -> tuple[bool, bool]:
    """(whether ollama is on PATH, whether this run installed it), asking before installing."""
    if shutil.which("ollama"):
        print(f"ollama installed: {shutil.which('ollama')}")
        return True, False
    if not wants_install():
        error("ollama was not installed, so omni stops here: it serves chat and embed.",
              f"  install it when you are ready, then run `omni init` again: {OLLAMA_DOWNLOAD_URL}")
        return False, False
    if install_ollama() and shutil.which("ollama"):
        print(f"ollama installed: {shutil.which('ollama')}")
        return True, True
    error("ollama install failed.",
          f"  install it by hand, then run `omni init` again: {OLLAMA_DOWNLOAD_URL}")
    return False, False


def answering(url: str) -> bool:
    """Whether the Ollama server answers at `url`, without printing anything."""
    try:
        httpx.get(f"{url}/api/version", timeout=2.0).raise_for_status()
    except (httpx.HTTPError, ValueError):
        return False
    return True


def start_ollama() -> subprocess.Popen | None:
    """Start the server the way this machine manages it; `check_ollama` explains it when that fails.

    Returns the server omni itself started, which omni stops again; a Homebrew service belongs to the
    machine and is left running.
    """
    if shutil.which("brew") and _brew_has_ollama():
        subprocess.run(["brew", "services", "start", "ollama"], check=False)
        return None
    if not shutil.which("ollama"):
        return None
    config.ensure_dirs()
    with (config.LOG_DIR / "ollama.log").open("ab") as logfile:
        return subprocess.Popen(guard.command(["ollama", "serve"]), stdin=subprocess.DEVNULL, stdout=logfile,
                                stderr=subprocess.STDOUT, start_new_session=True)


def stop_started_ollama(server: subprocess.Popen | None) -> None:
    """Stop the server `start_ollama` launched, so the gateway's supervisor owns the one that keeps running."""
    if server is None or server.poll() is not None:
        return
    server.terminate()
    try:
        server.wait(timeout=STOP_SERVER_SECONDS)
    except subprocess.TimeoutExpired:
        os.killpg(server.pid, signal.SIGKILL)
        server.wait()


def _brew_has_ollama() -> bool:
    """Whether Homebrew, not some other installer, owns the ollama binary on this machine."""
    result = subprocess.run(["brew", "list", "--formula", "ollama"], check=False, capture_output=True)
    return result.returncode == 0


def wait_for_server(url: str) -> None:
    """Give a server that was just started time to bind, then let `check_ollama` judge it."""
    deadline = time.monotonic() + SERVER_START_SECONDS
    while time.monotonic() < deadline:
        if answering(url):
            return
        time.sleep(PROBE_SECONDS)


def check_ollama(url: str) -> bool:
    """Whether the Ollama server answers at `url`, with the command to start it when it does not."""
    try:
        response = httpx.get(f"{url}/api/version", timeout=2.0)
        response.raise_for_status()
    except (httpx.HTTPError, ValueError):
        error(f"ollama is not answering at {url}.",
              "  start it, then run `omni init` again:",
              "    ollama serve                 # foreground",
              "    brew services start ollama   # background, macOS",
              f"  still nothing? {OLLAMA_TROUBLESHOOTING_URL}")
        if url != f"http://127.0.0.1:{config.OLLAMA_DEFAULT_PORT}":
            print(f"  omni is using {url} because OLLAMA_HOST is set", file=sys.stderr)
        return False
    version = ""
    try:
        version = response.json().get("version", "")
    except ValueError:
        pass
    print(f"ollama answering at {url}" + (f" (version {version})" if version else ""))
    return True


def model_url(model: str) -> str:
    """Where to read about `model`: its Hugging Face repo, or its page in the Ollama library."""
    if model.startswith("hf.co/"):
        return f"{HUGGINGFACE_URL}/{model.removeprefix('hf.co/').split(':')[0]}"
    return f"{OLLAMA_LIBRARY_URL}/{model.split(':')[0]}"


def pull(model: str) -> bool:
    """Download one model, explaining what to do when the download fails."""
    # flush so this lands before ollama's own progress, which it writes to the same fd
    print(f"pulling {model}", flush=True)
    try:
        done = subprocess.run(["ollama", "pull", model], check=False).returncode == 0
    except OSError as failure:
        error(f"could not run `ollama pull {model}`: {failure}",
              f"  reinstall ollama, then try again: {OLLAMA_DOWNLOAD_URL}")
        return False
    if done:
        return True
    error(f"`ollama pull {model}` failed.",
          f"  pull it by hand to see the whole error: ollama pull {model}",
          f"  the model's page: {model_url(model)}",
          f"  or change it in {config.USER_CONFIG}, then run `omni init` again")
    return False


def installed(url: str) -> set[str] | None:
    """Every model Ollama has on disk, or None when it stopped answering."""
    try:
        response = httpx.get(f"{url}/api/tags", timeout=5.0)
        response.raise_for_status()
        models = response.json().get("models", [])
    except (httpx.HTTPError, ValueError):
        return None
    return {model["name"] for model in models if isinstance(model, dict) and model.get("name")}


def download_line(downloads: list[dict], role: str) -> str | None:
    """One progress line for `role`'s in-flight download, from the gateway's /admin/models/downloads."""
    for entry in downloads:
        if entry.get("role") != role:
            continue
        completed_gb = (entry.get("completed") or 0) / GB
        total = entry.get("total")
        if not total:
            return f"  {role}: {completed_gb:.1f} GB downloaded"
        percent = 100 * (entry.get("completed") or 0) / total
        return f"  {role}: {completed_gb:.1f} / {total / GB:.1f} GB ({percent:.0f}%)"
    return None


def show_download(base: str, role: str) -> None:
    try:
        downloads = httpx.get(f"{base}/admin/models/downloads", timeout=5.0).json().get("downloads", [])
    except (httpx.HTTPError, ValueError):
        return
    line = download_line(downloads, role)
    if line:
        print(line, flush=True)


def load_with_progress(base: str, role: str) -> httpx.Response:
    """POST the load and, while it blocks on a weights download, print the progress the gateway tracks."""
    outcome: dict = {}

    def post() -> None:
        try:
            outcome["response"] = httpx.post(f"{base}/admin/models/load", json={"role": role, "force": True},
                                             timeout=MEDIA_LOAD_TIMEOUT)
        except Exception as failure:  # re-raised on the main thread below
            outcome["failure"] = failure

    # a daemon thread, so Ctrl-C exits at once instead of waiting out a 30-minute load
    request = threading.Thread(target=post, daemon=True)
    request.start()
    request.join(DOWNLOAD_PROGRESS_SECONDS)
    while request.is_alive():
        show_download(base, role)
        request.join(DOWNLOAD_PROGRESS_SECONDS)
    if "failure" in outcome:
        raise outcome["failure"]
    return outcome["response"]


def warm_media(host: str, port: int, wanted_roles: tuple[str, ...]) -> int:
    """Load each of `wanted_roles` the gateway serves, so the download is not paid by the first request.

    The gateway is started when it is not running: a worker only downloads its weights once it loads
    them, and nothing loads them without a request.
    """
    from omni.commands import lifecycle

    if not lifecycle.gateway_pid():
        code = lifecycle.up(host, port)
        if code:
            return code
    try:
        served = httpx.get(f"http://{host}:{port}/health", timeout=5.0).json().get("workers", {})
    except (httpx.HTTPError, ValueError):
        return error(f"omni is running but /health does not answer on {host}:{port}")
    roles = [role for role in wanted_roles if role in served]
    for role in wanted_roles:
        if role not in served:
            print(f"{role}: skipped, {registry.unavailable_reason(role) or 'its worker is not running'}")
    if not roles:
        return 0
    failed = 0
    for role in roles:
        print(f"loading {role} (weights download on first use)", flush=True)
        try:
            response = load_with_progress(f"http://{host}:{port}", role)
        except httpx.HTTPError as failure:
            print(f"{role}: {failure}", file=sys.stderr)
            failed += 1
            continue
        if response.status_code >= 400:
            print(f"{role}: {response.text}", file=sys.stderr)
            failed += 1
            continue
        print(f"{role}: loaded")
    return 1 if failed else 0


def plan_lines(settings: dict, all_media: bool) -> list[str]:
    """What this run downloads and what it leaves for later, so a multi-GB wait is never a surprise."""
    wanted = MEDIA_ROLES if all_media else SMALL_ROLES
    lines = ["omni init will download:",
             f"  chat, embed (Ollama): {', '.join(sorted({settings[role]['model'] for role in registry.OLLAMA_ROLES}))}"]
    for role in wanted:
        reason = registry.unavailable_reason(role)
        lines.append(f"  {role}: {settings[role]['model']}" + (f"  (skipped: {reason})" if reason else ""))
    if not all_media:
        lines.append("  image, video: not now (tens of GB); they download on first use, or run `omni init --media`")
    return lines


def run(all_media: bool = False) -> int:
    """Check the config, install Ollama if missing, pull chat and embed, then load decide, stt and tts.

    `all_media` loads image and video as well. Non-zero on failure. An Ollama this run started is
    stopped before it returns: the gateway starts and supervises its own.
    """
    started: list[subprocess.Popen] = []
    try:
        return _run(all_media, started)
    finally:
        for server in started:
            stop_started_ollama(server)


def _run(all_media: bool, started: list[subprocess.Popen]) -> int:
    try:
        settings = config.load()
    except config.ConfigError as failure:
        return error(f"omni: bad configuration: {failure}",
                     f"  fix {config.USER_CONFIG} and run `omni init` again",
                     f"  the shipped defaults are {config.CATALOG}")
    try:
        url = config.ollama_url()
    except config.ConfigError as failure:
        return error(f"omni: {failure}", "  fix or unset OLLAMA_HOST and run `omni init` again")

    for line in plan_lines(settings, all_media):
        print(line)
    present, installed_now = require_ollama()
    if not present:
        return 1
    # a fresh install is a fresh server: start it, so `omni init` can finish pulling in one go
    if installed_now and not answering(url):
        print(f"ollama is not answering at {url} yet; starting it", flush=True)
        server = start_ollama()
        if server is not None:
            started.append(server)
        wait_for_server(url)
    if not check_ollama(url):
        return 1

    models = sorted({settings[role]["model"] for role in registry.OLLAMA_ROLES})
    for model in models:
        if not pull(model):
            return 1

    present = installed(url)
    if present is None:
        return error(f"ollama stopped answering at {url}.",
                     f"  check the server, then run `omni init` again: {OLLAMA_TROUBLESHOOTING_URL}")
    have = {name.removesuffix(":latest") for name in present}
    missing = [model for model in models if model.removesuffix(":latest") not in have]
    if missing:
        return error(f"ollama does not list {', '.join(missing)} after pulling.",
                     f"  check `ollama list`, then pull by hand: ollama pull {missing[0]}",
                     f"  the model's page: {model_url(missing[0])}")

    host = settings["gateway"]["host"]
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"
    port = settings["gateway"]["port"]
    # before the gateway starts: it supervises ollama itself when none answers
    for server in started:
        stop_started_ollama(server)
    code = warm_media(host, port, MEDIA_ROLES if all_media else SMALL_ROLES)
    if code:
        return code
    print(f"ready: {', '.join(models)}")
    print(f"omni is running: open http://{host}:{port}/")
    if not all_media:
        print("  (the image and video models download on first use; `omni init --media` loads them now)")
    return 0
