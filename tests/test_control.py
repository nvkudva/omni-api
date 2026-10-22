"""The /admin controls for the process: server status, restart and stop, runtime settings, logs."""
import copy
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient

from omni import config, gateway
from omni.commands import lifecycle
from omni.supervisor.worker import Worker


def _error(response):
    body = response.json()
    assert {"message", "type"} <= set(body["error"]), body
    return body["error"]


class Control(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spawn = patch("omni.supervisor.pool.Supervisor.start", new=AsyncMock())
        cls.spawn.start()
        # the media workers exist only where their runtimes are installed; tests need them everywhere
        cls.runtimes = patch("omni.gateway.context.unavailable_reason", return_value=None)
        cls.runtimes.start()
        cls.client = TestClient(gateway.create(), base_url="http://localhost")
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        cls.runtimes.stop()
        cls.spawn.stop()

    def test_server_reports_gateway_ollama_and_tunables(self):
        body = self.client.get("/admin/server").json()
        self.assertIsInstance(body["gateway"]["pid"], int)
        self.assertIsInstance(body["gateway"]["version"], str)
        self.assertLessEqual(body["gateway"]["started_at"], time.time())
        self.assertIn("reachable", body["ollama"])
        self.assertIn("video.fps", body["tunables"])

    def test_server_restart_relaunches_detached(self):
        with patch.object(lifecycle, "relaunch") as relaunch:
            response = self.client.post("/admin/server/restart")
        self.assertEqual(response.status_code, 202)
        self.assertTrue(relaunch.call_args.kwargs["restart"])

    def test_server_stop_does_not_restart(self):
        with patch.object(lifecycle, "relaunch") as relaunch:
            response = self.client.post("/admin/server/stop")
        self.assertEqual(response.status_code, 202)
        self.assertFalse(relaunch.call_args.kwargs["restart"])

    def test_server_rejects_an_unknown_action(self):
        response = self.client.post("/admin/server/explode")
        self.assertEqual(response.status_code, 400)
        _error(response)

    def test_settings_saves_validates_and_restarts_the_affected_worker(self):
        built = []
        real_build = gateway.app.build
        with patch("omni.gateway.app.build", side_effect=lambda settings: built.append(real_build(settings)) or built[-1]):
            app = gateway.create()
        built[0].pool.workers.setdefault("video", Worker(name="video", argv=[], health_path="/health",
                                                         client=httpx.AsyncClient(base_url="http://worker")))
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(config, "USER_CONFIG", Path(directory) / "config.toml"), \
                patch("omni.supervisor.pool.Supervisor.restart", new=AsyncMock()) as restart, \
                TestClient(app, base_url="http://localhost") as client:
            response = client.post("/admin/settings", json={"section": "video", "key": "fps", "value": 12})
            saved = (Path(directory) / "config.toml").read_text()
        self.assertEqual(response.status_code, 200)
        self.assertIn("fps = 12", saved)
        self.assertEqual(response.json()["restarted"], ["video"])
        restart.assert_awaited_once_with("video")

    def test_settings_rejects_a_value_that_fails_validation(self):
        before = copy.deepcopy(self.client.get("/admin/server").json()["tunables"])
        response = self.client.post("/admin/settings", json={"section": "image", "key": "steps", "value": 0})
        self.assertEqual(response.status_code, 400)
        _error(response)
        self.assertEqual(self.client.get("/admin/server").json()["tunables"], before)

    def test_settings_rejects_a_key_that_is_not_tunable(self):
        response = self.client.post("/admin/settings", json={"section": "gateway", "key": "port", "value": 1})
        self.assertEqual(response.status_code, 400)
        _error(response)

    def test_logs_tail_the_gateway_log(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "omni.log").write_text("one\ntwo\nthree\n")
            with patch.object(config, "LOG_DIR", Path(directory)):
                body = self.client.get("/admin/logs", params={"lines": 2}).json()
        self.assertEqual(body["lines"], ["two", "three"])

    def test_logs_reject_a_bad_line_count(self):
        self.assertEqual(self.client.get("/admin/logs", params={"lines": 0}).status_code, 422)

    def test_role_log_tails_the_worker_log(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "video.log").write_text("one\ntwo\nthree\n")
            with patch.object(config, "LOG_DIR", Path(directory)):
                response = self.client.get("/admin/logs/video", params={"lines": 2})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, "two\nthree\n")

    def test_role_log_for_an_ollama_role_tails_its_own_log(self):
        with tempfile.TemporaryDirectory() as directory:
            own_log = Path(directory) / "requests" / "chat.log"
            own_log.parent.mkdir(parents=True)
            own_log.write_text("one\ntwo\nthree\n")
            with patch.object(config, "LOG_DIR", Path(directory)):
                response = self.client.get("/admin/logs/chat", params={"lines": 2})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, "two\nthree\n")

    def test_the_engine_log_tails_ollamas_server_log(self):
        with tempfile.TemporaryDirectory() as directory:
            server_log = Path(directory) / ".ollama" / "logs" / "server.log"
            server_log.parent.mkdir(parents=True)
            server_log.write_text("one\ntwo\nthree\n")
            with patch.object(Path, "home", classmethod(lambda cls: Path(directory))):
                response = self.client.get("/admin/logs/ollama", params={"lines": 2})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, "two\nthree\n")

    def test_role_log_rejects_an_unknown_role(self):
        self.assertEqual(self.client.get("/admin/logs/nope").status_code, 404)


if __name__ == "__main__":
    unittest.main()
