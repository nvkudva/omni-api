"""``omni init`` checks its own preconditions and turns every failure into the one thing to fix.

The command runs before anything else works, so its output is the only documentation a fresh machine
gets: each test here pins one failure to the guidance it must print.
"""
import contextlib
import io
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from omni import config
from omni.commands import init

SETTINGS = {
    "gateway": {"host": "127.0.0.1", "port": 10000, "idle_unload_minutes": 10.0},
    "chat": {"model": "llama3.2"},
    "decide": {"model": "llama3.2"},
    "embed": {"model": "embeddinggemma-2"},
}


class FakeResponse:
    def __init__(self, payload, error=None):
        self._payload = payload
        self._error = error

    def raise_for_status(self):
        if self._error is not None:
            raise self._error

    def json(self):
        return self._payload


def serve_api(models, version="0.12.3"):
    """An httpx.get that answers /api/version and /api/tags the way a running Ollama does."""
    def get(url, **_):
        if url.endswith("/api/version"):
            return FakeResponse({"version": version})
        if url.endswith("/api/tags"):
            return FakeResponse({"models": [{"name": f"{name}:latest"} for name in models]})
        raise AssertionError(url)
    return get


def which_from(*names):
    present = set(names)
    return lambda name: f"/usr/local/bin/{name}" if name in present else None


def ok_run(argv, check=False):  # mirrors subprocess.run's signature
    return SimpleNamespace(returncode=0)


def run_init(*, which, get=None, run=None, settings=SETTINGS, url="http://127.0.0.1:11434",
             load_error=None, url_error=None, all_media=False, warm_calls=None, warm_code=0):
    def load():
        if load_error is not None:
            raise load_error
        return settings

    def ollama_url():
        if url_error is not None:
            raise url_error
        return url

    def warm_media(host, port, wanted_roles):
        if warm_calls is not None:
            warm_calls.append((host, port, wanted_roles))
        return warm_code

    out, err = io.StringIO(), io.StringIO()
    with patch.object(init.shutil, "which", side_effect=which), \
         patch.object(init, "warm_media", side_effect=warm_media), \
         patch.object(init.sys.stdin, "isatty", return_value=False), \
         patch.object(init.subprocess, "run", side_effect=run or ok_run), \
         patch.object(init.httpx, "get", side_effect=get or serve_api([])), \
         patch.object(init.config, "load", side_effect=load), \
         patch.object(init.config, "ollama_url", side_effect=ollama_url), \
         contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = init.run(all_media=all_media)
    return code, out.getvalue(), err.getvalue()


class Checks(unittest.TestCase):
    def test_bad_config_names_the_file_to_fix(self):
        code, _, err = run_init(which=which_from("ollama"),
                                load_error=config.ConfigError("gateway.port must be between 1 and 65535"))
        self.assertEqual(code, 1)
        self.assertIn("bad configuration", err)
        self.assertIn("gateway.port must be between 1 and 65535", err)
        self.assertIn("config.toml", err)
        self.assertIn(str(config.CATALOG), err)

    def test_no_installer_points_at_the_download_page(self):
        code, _, err = run_init(which=which_from())
        self.assertEqual(code, 1)
        self.assertIn("no brew, curl or sh", err)
        self.assertIn(init.OLLAMA_DOWNLOAD_URL, err)

    def test_bad_ollama_host_is_reported_not_raised(self):
        code, _, err = run_init(which=which_from("ollama"),
                                url_error=config.ConfigError("OLLAMA_HOST has an invalid port: 'x'"))
        self.assertEqual(code, 1)
        self.assertIn("invalid port", err)
        self.assertIn("OLLAMA_HOST", err)

    def test_ollama_not_answering_says_how_to_start_it(self):
        def get(url, **_):
            raise httpx.ConnectError("connection refused")

        code, _, err = run_init(which=which_from("ollama"), get=get)
        self.assertEqual(code, 1)
        self.assertIn("not answering", err)
        self.assertIn("ollama serve", err)
        self.assertIn("brew services start ollama", err)

    def test_custom_ollama_host_is_explained(self):
        def get(url, **_):
            raise httpx.ConnectError("connection refused")

        code, _, err = run_init(which=which_from("ollama"), get=get, url="http://10.0.0.5:11434")
        self.assertEqual(code, 1)
        self.assertIn("OLLAMA_HOST is set", err)


class Pulling(unittest.TestCase):
    def test_installs_ollama_with_brew_then_pulls_each_model(self):
        installed = set()
        calls = []

        def which(name):
            if name == "ollama":
                return "/usr/local/bin/ollama" if installed else None
            return {"brew": "/opt/homebrew/bin/brew"}.get(name)

        def run(argv, check=False):
            calls.append(argv)
            if argv[:2] == ["brew", "install"]:
                installed.add("ollama")
            return SimpleNamespace(returncode=0)

        code, _, err = run_init(which=which, run=run,
                                get=serve_api(["llama3.2", "embeddinggemma-2"]))
        self.assertEqual(code, 0, err)
        self.assertIn(["brew", "install", "ollama"], calls)
        pulls = [call for call in calls if call[:2] == ["ollama", "pull"]]
        self.assertEqual(pulls, [["ollama", "pull", "embeddinggemma-2"], ["ollama", "pull", "llama3.2"]])

    def test_pull_failure_points_at_the_model_page_and_config(self):
        def run(argv, check=False):
            return SimpleNamespace(returncode=1 if argv[:2] == ["ollama", "pull"] else 0)

        code, _, err = run_init(which=which_from("ollama"), run=run, get=serve_api([]))
        self.assertEqual(code, 1)
        self.assertIn("failed.", err)
        self.assertIn(f"{init.OLLAMA_LIBRARY_URL}/embeddinggemma-2", err)
        self.assertIn("config.toml", err)

    def test_a_missing_ollama_at_pull_time_fails_loudly(self):
        def run(argv, check=False):
            raise FileNotFoundError("ollama")

        code, _, err = run_init(which=which_from("ollama"), run=run, get=serve_api([]))
        self.assertEqual(code, 1)
        self.assertIn("could not run", err)
        self.assertIn(init.OLLAMA_DOWNLOAD_URL, err)

    def test_verification_catches_a_model_ollama_never_lists(self):
        code, _, err = run_init(which=which_from("ollama"), get=serve_api([]))
        self.assertEqual(code, 1)
        self.assertIn("does not list", err)
        self.assertIn("embeddinggemma-2", err)

    def test_success_reports_ready_and_the_playground_url(self):
        code, out, err = run_init(which=which_from("ollama"),
                                  get=serve_api(["llama3.2", "embeddinggemma-2"]))
        self.assertEqual(code, 0, err)
        self.assertIn("ready: embeddinggemma-2, llama3.2", out)
        self.assertIn("http://127.0.0.1:10000/", out)

    def test_success_downloads_and_starts_the_speech_models(self):
        warm_calls = []
        code, _, err = run_init(which=which_from("ollama"), warm_calls=warm_calls,
                                get=serve_api(["llama3.2", "embeddinggemma-2"]))
        self.assertEqual(code, 0, err)
        self.assertEqual(warm_calls, [("127.0.0.1", 10000, ("stt", "tts"))])

    def test_media_flag_also_loads_image_and_video(self):
        warm_calls = []
        code, _, err = run_init(which=which_from("ollama"), warm_calls=warm_calls, all_media=True,
                                get=serve_api(["llama3.2", "embeddinggemma-2"]))
        self.assertEqual(code, 0, err)
        self.assertEqual(warm_calls, [("127.0.0.1", 10000, ("stt", "tts", "image", "video"))])

    def test_a_speech_model_that_fails_to_load_fails_init(self):
        code, out, _ = run_init(which=which_from("ollama"), warm_code=1,
                                get=serve_api(["llama3.2", "embeddinggemma-2"]))
        self.assertEqual(code, 1)
        self.assertNotIn("ready:", out)


class ModelUrls(unittest.TestCase):
    def test_ollama_model(self):
        self.assertEqual(init.model_url("llama3.2:latest"), "https://ollama.com/library/llama3.2")

    def test_hugging_face_model(self):
        self.assertEqual(init.model_url("hf.co/mradermacher/OneJev1.5-0.8B-GGUF:Q8_0"),
                         "https://huggingface.co/mradermacher/OneJev1.5-0.8B-GGUF")


if __name__ == "__main__":
    unittest.main()
