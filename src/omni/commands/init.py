"""``omni init``: make sure Ollama is installed and pull the three models it serves."""
import shutil
import subprocess
import sys

import httpx

from omni import config


def install_ollama() -> int:
    if shutil.which("brew"):
        argv = ["brew", "install", "ollama"]
    else:
        argv = ["sh", "-c", "curl -fsSL https://ollama.com/install.sh | sh"]
    print(f"ollama not found; installing: {' '.join(argv)}")
    return subprocess.run(argv, check=False).returncode


def run() -> int:
    """Install Ollama if missing, then pull chat, decide and embed. Non-zero on failure."""
    if shutil.which("ollama"):
        print(f"ollama installed: {shutil.which('ollama')}")
    elif install_ollama() != 0 or not shutil.which("ollama"):
        print("ollama install failed; see https://ollama.com/download", file=sys.stderr)
        return 1

    ollama_url = config.ollama_url()
    try:
        httpx.get(f"{ollama_url}/api/version", timeout=2.0).raise_for_status()
    except httpx.HTTPError:
        print(f"ollama is not running at {ollama_url}; start it, then run `omni init` again", file=sys.stderr)
        return 1

    try:
        settings = config.load()
    except config.ConfigError as error:
        print(f"omni: bad configuration: {error}", file=sys.stderr)
        return 1

    models = {settings[role]["model"] for role in ("chat", "decide", "embed")}
    for model in sorted(models):
        print(f"pulling {model}")
        if subprocess.run(["ollama", "pull", model], check=False).returncode != 0:
            return 1
    return 0
