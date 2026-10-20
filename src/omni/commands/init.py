"""``omni init``: check the setup, install Ollama if it is missing, then pull the models it serves.

Every step states its own precondition and, when it fails, the one thing to fix — the command to run
and a link — so a fresh machine can go from clone to ``omni up`` without reading the source.
"""
import shutil
import subprocess
import sys

import httpx

from omni import config

# chat, decide and embed run in the machine's Ollama; the MLX roles download on first use instead.
OLLAMA_ROLES = ("chat", "decide", "embed")

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


def install_ollama() -> bool:
    """Install Ollama with whatever this machine has, or False when there is no route to one."""
    if shutil.which("brew"):
        argv = ["brew", "install", "ollama"]
    elif shutil.which("curl") and shutil.which("sh"):
        argv = ["sh", "-c", f"curl -fsSL {OLLAMA_INSTALL_SCRIPT} | sh"]
    else:
        print("ollama is not installed and there is no brew, curl or sh here to install it with",
              file=sys.stderr)
        return False
    print(f"ollama not found; installing: {' '.join(argv)}", flush=True)
    try:
        return subprocess.run(argv, check=False).returncode == 0
    except OSError as failure:
        print(f"could not run {' '.join(argv)}: {failure}", file=sys.stderr)
        return False


def require_ollama() -> bool:
    """Whether the ollama binary is on PATH, installing it first if it is missing."""
    if shutil.which("ollama"):
        print(f"ollama installed: {shutil.which('ollama')}")
        return True
    if install_ollama() and shutil.which("ollama"):
        print(f"ollama installed: {shutil.which('ollama')}")
        return True
    error("ollama install failed.",
          f"  install it by hand, then run `omni init` again: {OLLAMA_DOWNLOAD_URL}")
    return False


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


# A smaller default per role, for a small machine or a quick start. `omni init --small`
# writes these to ~/.omni-api/config.toml; they stay the default until changed there or in
# the playground. Image has no smaller option: this build runs Qwen-Image only, and the
# Turbo checkpoint is already its cheapest form.
SMALL = {
    "chat": "qwen2.5:0.5b",
    "decide": "laya",
    "embed": "embeddinggemma-2:270m",
    "stt": "moondream/parakeet-redux",
    "tts": "mlx-community/Kokoro-82M-8bit",
    "video": "baa-ai/LTX-2.3-22B-RAM-12GB-MLX",
}


def installed(url: str) -> set[str] | None:
    """Every model Ollama has on disk, or None when it stopped answering."""
    try:
        response = httpx.get(f"{url}/api/tags", timeout=5.0)
        response.raise_for_status()
        models = response.json().get("models", [])
    except (httpx.HTTPError, ValueError):
        return None
    return {model["name"] for model in models if isinstance(model, dict) and model.get("name")}


def run(small: bool = False) -> int:
    """Check the config, install Ollama if missing, then pull chat, decide and embed. Non-zero on failure.

    With `small`, the smallest model per role becomes the default: the Ollama ones are pulled
    here, the media ones are written to the config and download on first use.
    """
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

    if not require_ollama() or not check_ollama(url):
        return 1

    chosen = SMALL if small else {}
    if small:
        print("small profile: " + ", ".join(f"{role}={model}" for role, model in chosen.items()))
    models = sorted({chosen.get(role, settings[role]["model"]) for role in OLLAMA_ROLES})
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

    if small:
        # Only after every pull worked, so a failed run never leaves half a profile behind.
        for role, model in chosen.items():
            config.save_override(role, "model", model)
        print(f"small defaults written to {config.USER_CONFIG}; restart to apply: `omni down` then `omni up`")

    host = settings["gateway"]["host"]
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"
    print(f"ready: {', '.join(models)}")
    print(f"next: `omni up`, then open http://{host}:{settings['gateway']['port']}/")
    print("  (the stt, tts, image and video models download on first use)")
    return 0
