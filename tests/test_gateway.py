"""The gateway app end to end, with process spawning stubbed out.

Every response must use the one OpenAI error shape, whatever the failure.
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient

from omni import config, gateway
from omni.gateway import residency
from omni.registry import ROLES


def _error(response):
    body = response.json()
    assert set(body) == {"error"}, body
    # every error carries a message and a type; a refusal may add the blockers beside them
    assert {"message", "type"} <= set(body["error"]), body
    return body["error"]


class Gateway(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # never spawn real worker processes; the app under test is only the HTTP surface
        cls.spawn = patch("omni.supervisor.pool.Supervisor.start", new=AsyncMock())
        cls.spawn.start()
        cls.client = TestClient(gateway.create())
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        cls.spawn.stop()

    def test_root_redirects_to_the_playground(self):
        response = self.client.get("/", follow_redirects=False)
        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], "/playground")

    def test_playground_serves_the_built_page(self):
        response = self.client.get("/playground")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["content-type"])

    def test_health_lists_every_worker(self):
        body = self.client.get("/health").json()
        self.assertIn("ok", body)
        self.assertIsInstance(body["ok"], bool)
        self.assertIsInstance(body["workers"], dict)

    def test_models_returns_defaults_for_every_role(self):
        body = self.client.get("/v1/models").json()
        self.assertEqual(body["object"], "list")
        self.assertIsInstance(body["data"], list)
        self.assertEqual(set(body["defaults"]), set(ROLES))

    def test_models_never_labels_the_embed_model_as_chat(self):
        body = self.client.get("/v1/models").json()
        ids = [entry["id"] for entry in body["data"]]
        self.assertEqual(len(ids), len(set(ids)), ids)
        for entry in body["data"]:
            if entry["id"].startswith("embeddinggemma"):
                self.assertEqual(entry["role"], "embed", entry)
            if entry["capabilities"] and "completion" not in entry["capabilities"]:
                self.assertNotEqual(entry["role"], "chat", entry)

    def test_chat_rejects_the_embed_model(self):
        embed = self.client.get("/v1/models").json()["defaults"]["embed"]
        response = self.client.post("/v1/chat/completions",
                                    json={"model": embed, "messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(response.status_code, 400)
        self.assertIn("does not support chat", _error(response)["message"])

    def test_decide_rejects_the_embed_model(self):
        embed = self.client.get("/v1/models").json()["defaults"]["embed"]
        response = self.client.post("/v1/systemone", json={
            "model": embed, "state": "hi",
            "questions": {"q": {"type": "noul", "instructions": "is it true?", "criteria": {"true": "yes", "false": "no"}}},
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn("embed model", _error(response)["message"])

    def test_embeddings_rejects_the_chat_model(self):
        defaults = self.client.get("/v1/models").json()["defaults"]
        response = self.client.post("/v1/embeddings", json={"model": defaults["chat"], "input": "hi"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("does not support embeddings", _error(response)["message"])

    def test_chat_rejects_the_embed_model_with_latest_tag(self):
        embed = self.client.get("/v1/models").json()["defaults"]["embed"]
        response = self.client.post("/v1/chat/completions",
                                    json={"model": f"{embed}:latest", "messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(response.status_code, 400)

    def test_admin_models_lists_the_roles(self):
        body = self.client.get("/admin/models").json()
        self.assertEqual(set(body["roles"]), set(ROLES))
        for entry in body["roles"].values():
            self.assertIn("default", entry)
            self.assertIn("runner", entry)

    def test_admin_models_lists_installed_per_role(self):
        body = self.client.get("/admin/models").json()
        self.assertEqual(set(body["installed_by_role"]), set(ROLES))

    def test_admin_status_reports_a_state_for_every_role(self):
        body = self.client.get("/admin/models/status").json()
        self.assertEqual(set(body["roles"]), set(ROLES))
        for role, entry in body["roles"].items():
            self.assertIn(entry["state"], ("ready", "missing", "incompatible"), role)
            self.assertIsInstance(entry["installed"], list)
            self.assertIsInstance(entry["total_bytes"], int)
            if entry["state"] == "ready":
                self.assertIsNone(entry["reason"])
            else:
                self.assertTrue(entry["reason"], role)

    def test_admin_check_refuses_a_repo_the_runtime_cannot_run(self):
        response = self.client.get("/admin/models/check",
                                   params={"role": "video", "model": "https://huggingface.co/Qwen/Qwen-Image-2.1"})
        body = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(body["supported"])
        self.assertEqual(body["model"], "Qwen/Qwen-Image-2.1")
        self.assertIn("ltx", body["reason"])

    def test_admin_check_rejects_an_unknown_role(self):
        response = self.client.get("/admin/models/check", params={"role": "bogus", "model": "x/y"})
        self.assertEqual(response.status_code, 400)

    def test_admin_workers_all_restart_is_accepted(self):
        response = self.client.post("/admin/workers/all/restart")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["worker"], "all")

    def test_admin_workers_all_rejects_start(self):
        response = self.client.post("/admin/workers/all/start")
        self.assertEqual(response.status_code, 400)

    def test_unknown_route_is_the_error_shape(self):
        response = self.client.get("/nope")
        self.assertEqual(response.status_code, 404)
        self.assertIn("message", _error(response))

    def test_chat_rejects_a_non_json_body(self):
        response = self.client.post("/v1/chat/completions", content=b"not json",
                                    headers={"content-type": "application/json"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(_error(response)["type"], "invalid_request_error")

    def test_decide_rejects_a_missing_question_map(self):
        response = self.client.post("/v1/systemone", json={})
        self.assertEqual(response.status_code, 400)
        self.assertIn("questions", _error(response)["message"])

    def test_embeddings_rejects_a_bad_input_type(self):
        response = self.client.post("/v1/embeddings", json={"input": "hi", "input_type": "nope"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("input_type", _error(response)["message"])

    def test_admin_search_rejects_an_unknown_role(self):
        response = self.client.get("/admin/models/search", params={"role": "bogus"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("role", _error(response)["message"])

    def test_query_validation_uses_the_error_shape(self):
        response = self.client.get("/admin/models/search", params={"role": "chat", "limit": 0})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(_error(response)["type"], "invalid_request_error")

    def test_admin_worker_unknown_name(self):
        response = self.client.post("/admin/workers/nope/start")
        self.assertEqual(response.status_code, 404)

    def test_worker_action_must_be_known(self):
        # 'nope' is not a worker either way, so the name check fires first; this documents the order
        response = self.client.post("/admin/workers/chat/delete")
        self.assertIn(response.status_code, (400, 404, 409))

    def test_admin_load_reports_the_workers_own_loaded_flag(self):
        """The worker's report is the truth: a load that left nothing resident must not claim `loaded`."""
        report = httpx.Response(200, json={"loaded": False},
                                request=httpx.Request("POST", "http://worker/load"))
        with patch.object(httpx.AsyncClient, "post", new=AsyncMock(return_value=report)):
            response = self.client.post("/admin/models/load", json={"role": "stt", "force": True})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["loaded"])

    def test_admin_unload_reaches_a_transport_failure_as_a_restart(self):
        with patch.object(httpx.AsyncClient, "post", new=AsyncMock(side_effect=httpx.ConnectError("no socket"))):
            response = self.client.post("/admin/models/unload", json={"role": "stt"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(_error(response)["type"], "unavailable")

    def test_an_ambient_warning_advises_but_never_gates_a_load(self):
        """Machine-wide pressure is the same whatever you load, so it must not ask for confirmation."""
        plan = residency.Plan(["6.4 GB swapped out: models will run slowly"], [], None)
        with patch.object(residency, "plan", new=AsyncMock(return_value=plan)), \
                patch.object(residency, "set_residency", new=AsyncMock(return_value=True)):
            response = self.client.post("/admin/models/load", json={"role": "stt"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["advisories"], ["6.4 GB swapped out: models will run slowly"])

    def test_a_blocker_about_this_model_still_asks_for_force(self):
        plan = residency.Plan([], ["needs 40.0 GB of disk; only 5.0 GB free"], None)
        with patch.object(residency, "plan", new=AsyncMock(return_value=plan)), \
                patch.object(residency, "set_residency", new=AsyncMock(return_value=True)):
            response = self.client.post("/admin/models/load", json={"role": "stt"})
        self.assertEqual(response.status_code, 409)
        error = _error(response)
        self.assertEqual(error["type"], "overload")
        # the blockers travel as a list, so the client does not have to re-parse the sentence
        self.assertEqual(error["blockers"], ["needs 40.0 GB of disk; only 5.0 GB free"])

    def test_pinning_records_the_role_and_health_reports_it(self):
        with tempfile.TemporaryDirectory() as directory:
            pins = Path(directory) / "pins.json"
            with patch.object(config, "PINS", pins), \
                    patch.object(residency, "plan", new=AsyncMock(return_value=residency.Plan([], [], None))), \
                    patch.object(residency, "set_residency", new=AsyncMock(return_value=True)):
                response = self.client.post("/admin/pins", json={"role": "stt", "pinned": True})
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.json()["pinned"])
                self.assertEqual(self.client.get("/health").json()["pins"], ["stt"])
                again = self.client.post("/admin/pins", json={"role": "stt", "pinned": False})
                self.assertFalse(again.json()["pinned"])
                self.assertEqual(self.client.get("/health").json()["pins"], [])

    def test_pinning_an_uninstalled_ollama_model_is_a_conflict(self):
        fatal = residency.Plan([], [], "qwen2.5:3b is not installed; install it first")
        with patch.object(residency, "plan", new=AsyncMock(return_value=fatal)), \
                patch.object(residency, "set_residency", new=AsyncMock()):
            response = self.client.post("/admin/pins", json={"role": "chat", "pinned": True})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(_error(response)["type"], "conflict")

    def test_a_pin_stands_even_when_the_worker_is_not_up_yet(self):
        with tempfile.TemporaryDirectory() as directory:
            pins = Path(directory) / "pins.json"
            with patch.object(config, "PINS", pins), \
                    patch.object(residency, "plan", new=AsyncMock(return_value=residency.Plan([], [], None))), \
                    patch.object(residency, "set_residency",
                                 new=AsyncMock(side_effect=httpx.ConnectError("no socket"))):
                response = self.client.post("/admin/pins", json={"role": "stt", "pinned": True})
                self.assertTrue(response.json()["pinned"])
                self.assertFalse(response.json()["loaded"])
                self.assertEqual(config.load_pins(), ["stt"])
            self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
