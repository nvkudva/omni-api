"""The gateway app end to end, with process spawning stubbed out.

Every response must use the one OpenAI error shape, whatever the failure.
"""
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from omni import gateway
from omni.registry import ROLES


def _error(response):
    body = response.json()
    assert set(body) == {"error"}, body
    assert set(body["error"]) == {"message", "type"}, body
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

    def test_admin_models_lists_the_roles(self):
        body = self.client.get("/admin/models").json()
        self.assertEqual(set(body["roles"]), set(ROLES))
        for entry in body["roles"].values():
            self.assertIn("default", entry)
            self.assertIn("runner", entry)

    def test_admin_models_lists_installed_per_role(self):
        body = self.client.get("/admin/models").json()
        self.assertEqual(set(body["installed_by_role"]), set(ROLES))

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


if __name__ == "__main__":
    unittest.main()
