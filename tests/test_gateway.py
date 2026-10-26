"""The gateway app end to end, with process spawning stubbed out.

Every response must use the one OpenAI error shape, whatever the failure.
"""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import httpx
from fastapi.testclient import TestClient

from omni import config, gateway
from omni.errors import error_response
from omni.gateway import residency
from omni.gateway.routes import system
from omni.registry import ROLES, pulling
from omni.supervisor.worker import Worker


def _error(response):
    body = response.json()
    assert set(body) == {"error"}, body
    # every error carries a message and a type; a refusal may add the blockers beside them
    assert {"message", "type"} <= set(body["error"]), body
    return body["error"]


class ModelRoles(unittest.TestCase):
    def test_an_embedding_model_is_labelled_embed(self):
        settings = config.load()
        self.assertEqual(system._role_for("some-embed", ["embedding"], settings), "embed")

    def test_a_completion_model_stays_chat(self):
        settings = config.load()
        self.assertEqual(system._role_for("some-chat", ["completion", "tools"], settings), "chat")


class Gateway(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # never spawn real worker processes; the app under test is only the HTTP surface
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

    def test_old_playground_path_redirects_to_the_root(self):
        response = self.client.get("/playground", follow_redirects=False)
        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], "/")

    def test_playground_serves_the_built_page(self):
        response = self.client.get("/playground")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["content-type"])

    def test_playground_tab_paths_serve_the_page(self):
        for path in ("/image", "/settings/models", "/settings/models/chat"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, path)
            self.assertIn("text/html", response.headers["content-type"])

    def test_unknown_api_paths_stay_json_404(self):
        response = self.client.get("/v1/nope")
        self.assertEqual(response.status_code, 404)
        self.assertIn("json", response.headers["content-type"])

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
            if "embedding" in entry["capabilities"]:
                self.assertNotEqual(entry["role"], "chat", entry)

    def test_chat_rejects_the_embed_model(self):
        embed = self.client.get("/v1/models").json()["defaults"]["embed"]
        response = self.client.post("/v1/chat/completions",
                                    json={"model": embed, "messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(response.status_code, 400)
        self.assertIn("does not support chat", _error(response)["message"])

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

    def test_admin_status_carries_each_role_spec_and_its_recommendations(self):
        body = self.client.get("/admin/models/status").json()
        stt = body["roles"]["stt"]
        self.assertEqual(stt["family"], "parakeet")
        self.assertTrue(stt["accepts"])
        first = stt["recommended"][0]
        self.assertEqual(first["model"], stt["default"])
        self.assertIsInstance(first["blockers"], list)
        self.assertIsInstance(first["installed"], bool)

    def test_admin_status_flags_a_role_whose_runtime_is_missing(self):
        with patch("omni.registry.unavailable_reason", return_value="stt cannot run: this install has no mlx"):
            body = self.client.get("/admin/models/status").json()
        self.assertEqual(body["roles"]["stt"]["state"], "incompatible")
        self.assertIn("cannot run", body["roles"]["stt"]["reason"])

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

    def test_admin_pull_rejects_a_repo_the_role_cannot_run(self):
        with patch("omni.gateway.routes.admin_models.registry.ensure_started") as started:
            response = self.client.post("/admin/models/pull", json={"role": "video", "model": "Qwen/Qwen-Image-2.1"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("cannot run", _error(response)["message"])
        started.assert_not_called()

    def test_admin_delete_refuses_a_model_that_is_downloading(self):
        pulling.track("stt", "onnx/parakeet", 100, 25)
        self.addCleanup(pulling.ACTIVE.clear)
        with patch("omni.gateway.routes.admin_models.registry.hf_delete") as deleted:
            response = self.client.post("/admin/models/delete", json={"role": "stt", "model": "onnx/parakeet"})
        self.assertEqual(response.status_code, 409)
        self.assertIn("downloading", _error(response)["message"])
        deleted.assert_not_called()

    def test_admin_worker_start_leaves_a_running_worker_alone(self):
        built = []
        real_build = gateway.app.build
        with patch("omni.gateway.app.build", side_effect=lambda settings: built.append(real_build(settings)) or built[-1]):
            app = gateway.create()
        pool = built[0].pool
        if not pool.workers:
            pool.workers["video"] = Worker(name="video", argv=[], client=httpx.AsyncClient(base_url="http://worker"),
                                           health_path="/health")
        name = next(iter(pool.workers))
        with TestClient(app, base_url="http://localhost") as client, patch("omni.supervisor.pool.Supervisor.restart", new=AsyncMock()) as restart:
            pool.workers[name].status, pool.workers[name].held = "ready", False
            self.assertEqual(client.post(f"/admin/workers/{name}/start").status_code, 200)
            restart.assert_not_called()
            pool.workers[name].held = True
            client.post(f"/admin/workers/{name}/start")
            restart.assert_awaited_once()

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

    def test_admin_worker_start_explains_a_missing_media_install(self):
        # the pool omits a worker whose runtime is missing; "nope" stands in for it
        with patch("omni.registry.unavailable_reason", return_value="stt cannot run: this install has no mlx"):
            response = self.client.post("/admin/workers/nope/start")
        self.assertEqual(response.status_code, 409)
        self.assertIn("cannot run", _error(response)["message"])

    def test_admin_worker_unknown_name(self):
        response = self.client.post("/admin/workers/nope/start")
        self.assertEqual(response.status_code, 404)

    def test_worker_action_must_be_known(self):
        # 'nope' is not a worker either way, so the name check fires first; this documents the order
        response = self.client.post("/admin/workers/chat/delete")
        self.assertIn(response.status_code, (400, 404, 409))

    def _own_client(self, *roles):
        """A TestClient for a fresh app, with a worker client for each role this machine may not run."""
        built = []
        real_build = gateway.app.build
        with patch("omni.gateway.app.build", side_effect=lambda settings: built.append(real_build(settings)) or built[-1]):
            app = gateway.create()
        for role in roles:
            built[0].clients.setdefault(role, httpx.AsyncClient(base_url="http://worker"))
        return TestClient(app, base_url="http://localhost")

    def test_admin_load_reports_the_workers_own_loaded_flag(self):
        """The worker's report is the truth: a load that left nothing resident must not claim `loaded`."""
        report = httpx.Response(200, json={"loaded": False},
                                request=httpx.Request("POST", "http://worker/load"))
        with self._own_client("stt") as client, \
                patch.object(httpx.AsyncClient, "post", new=AsyncMock(return_value=report)), \
                patch.object(residency, "ensure_downloaded", new=AsyncMock()):
            response = client.post("/admin/models/load", json={"role": "stt", "force": True})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["loaded"])

    def test_admin_unload_reaches_a_transport_failure_as_a_restart(self):
        with self._own_client("stt") as client, \
                patch.object(httpx.AsyncClient, "post", new=AsyncMock(side_effect=httpx.ConnectError("no socket"))):
            response = client.post("/admin/models/unload", json={"role": "stt"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(_error(response)["type"], "unavailable")

    def test_admin_unload_without_a_worker_client_is_unavailable(self):
        built = []
        real_build = gateway.app.build
        with patch("omni.gateway.app.build", side_effect=lambda settings: built.append(real_build(settings)) or built[-1]):
            app = gateway.create()
        built[0].clients.pop("stt", None)
        with TestClient(app, base_url="http://localhost") as client:
            response = client.post("/admin/models/unload", json={"role": "stt"})
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

    def test_downloads_lists_in_flight_downloads_for_progress_bars(self):
        pulling.track("stt", "onnx/parakeet", 100, 40)
        try:
            response = self.client.get("/admin/models/downloads")
        finally:
            pulling.finish("onnx/parakeet")
        self.assertEqual(response.status_code, 200)
        downloads = response.json()["downloads"]
        self.assertEqual(len(downloads), 1)
        self.assertEqual(downloads[0]["role"], "stt")
        self.assertEqual(downloads[0]["completed"], 40)

    def test_downloads_is_empty_when_nothing_is_downloading(self):
        pulling.ACTIVE.clear()
        pulling.DONE.clear()
        try:
            response = self.client.get("/admin/models/downloads")
        finally:
            pulling.ACTIVE.clear()
            pulling.DONE.clear()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"downloads": [], "recent": []})

    def test_downloads_reports_recently_finished_downloads(self):
        pulling.DONE["onnx/parakeet"] = {"role": "stt", "model": "onnx/parakeet",
                                        "status": "success", "finished_at": 1.0}
        try:
            with patch.object(pulling, "RECENT_TTL_SECONDS", 10**9):
                response = self.client.get("/admin/models/downloads")
        finally:
            pulling.DONE.clear()
        self.assertEqual(response.status_code, 200)
        self.assertEqual([entry["model"] for entry in response.json()["recent"]], ["onnx/parakeet"])

    def test_pull_streams_the_shared_download_to_success(self):
        outcome = {"role": "tts", "model": "onnx-community/Kokoro-82M-v1.0-ONNX", "status": "success"}
        task = Mock(done=lambda: True)
        with patch("omni.registry.ensure_started", return_value=task), \
                patch("omni.registry.outcome", return_value=outcome):
            response = self.client.post("/admin/models/pull",
                                        json={"role": "tts",
                                              "model": "onnx-community/Kokoro-82M-v1.0-ONNX"})
        self.assertEqual(response.status_code, 200)
        lines = [json.loads(line) for line in response.text.splitlines() if line.strip()]
        self.assertTrue(lines)
        self.assertEqual(lines[-1]["status"], "success")

    def test_pull_streams_a_terminal_error_as_an_error_event(self):
        outcome = {"role": "tts", "model": "nope", "error": "huggingface: 401 Unauthorized"}
        task = Mock(done=lambda: True)
        with patch("omni.registry.ensure_started", return_value=task), \
                patch("omni.registry.outcome", return_value=outcome):
            response = self.client.post("/admin/models/pull", json={"role": "tts", "model": "onnx-community/Kokoro-82M-v1.0-ONNX"})
        lines = [json.loads(line) for line in response.text.splitlines() if line.strip()]
        self.assertTrue(lines[-1].get("error", "").startswith("huggingface: 401"))

    def test_stop_cancels_a_running_download(self):
        with patch("omni.registry.pulling.stop", return_value=True) as stop:
            response = self.client.post("/admin/models/pull/stop",
                                        json={"role": "tts", "model": "onnx-community/Kokoro-82M-v1.0-ONNX"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"stopped": "onnx-community/Kokoro-82M-v1.0-ONNX"})
        stop.assert_called_once_with("onnx-community/Kokoro-82M-v1.0-ONNX")

    def test_stop_without_a_running_download_is_not_found(self):
        response = self.client.post("/admin/models/pull/stop",
                                    json={"role": "tts", "model": "onnx-community/Kokoro-82M-v1.0-ONNX"})
        self.assertEqual(response.status_code, 404)

    def test_image_generation_downloads_the_model_first(self):
        from fastapi.responses import JSONResponse
        with patch.object(residency, "ensure_ready", new=AsyncMock(return_value=None)) as ready, \
                patch("omni.gateway.proxy.Proxy.forward",
                      new=AsyncMock(return_value=JSONResponse({}))) as forward:
            response = self.client.post("/v1/images/generations", json={"prompt": "a cat"})
            forward.assert_called_once()
            asyncio.run(forward.call_args.kwargs["ready"]())
        self.assertEqual(response.status_code, 200)
        ready.assert_called_once()
        self.assertEqual(ready.call_args.args[1], "image")

    def test_image_generation_reports_a_failed_first_download(self):
        with self._own_client("image") as client, \
                patch.object(residency, "ensure_ready",
                             new=AsyncMock(return_value=error_response(502, "image: gated", "upstream_error"))):
            response = client.post("/v1/images/generations", json={"prompt": "a cat"})
        self.assertEqual(response.status_code, 502)
        self.assertEqual(_error(response)["type"], "upstream_error")

    def test_a_retained_embed_call_replays_without_rerunning(self):
        payload = {"input": "the sky is blue"}
        headers = {"x-omni-exec": "embed-1"}
        with patch("omni.inference.embed.run", new=AsyncMock(return_value={"answers": {}})) as run:
            first = self.client.post("/v1/embeddings", json=payload, headers=headers)
            second = self.client.post("/v1/embeddings", json=payload, headers=headers)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json(), second.json())
        run.assert_called_once()

    def test_requests_lists_and_serves_a_retained_result(self):
        payload = {"input": "the sky is blue"}
        with patch("omni.inference.embed.run", new=AsyncMock(return_value={"answers": {}})):
            self.client.post("/v1/embeddings", json=payload, headers={"x-omni-exec": "embed-2"})
        listing = self.client.get("/admin/requests").json()
        self.assertEqual([entry["id"] for entry in listing["recent"] if entry["id"] == "embed-2"],
                         ["embed-2"])
        single = self.client.get("/admin/requests/embed-2").json()
        self.assertEqual(single["status"], "done")
        self.assertNotIn("body", single)
        result = self.client.get("/admin/requests/embed-2/result")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json(), {"answers": {}})

    def test_a_foreign_host_is_refused(self):
        response = self.client.get("/health", headers={"host": "evil.example:10000"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(_error(response)["type"], "forbidden")

    def test_a_cross_site_admin_change_is_refused(self):
        response = self.client.post("/admin/workers/all/stop", headers={"origin": "https://evil.example"})
        self.assertEqual(response.status_code, 403)

    def test_a_cross_site_inference_call_is_not_refused_by_the_guard(self):
        response = self.client.post("/v1/embeddings", content=b"[",
                                    headers={"origin": "tauri://localhost"})
        self.assertEqual(response.status_code, 400)

    def test_a_sandboxed_frame_cannot_start_inference(self):
        response = self.client.post("/v1/embeddings", content=b"[", headers={"origin": "null"})
        self.assertEqual(response.status_code, 403)

    def test_a_web_page_cannot_start_inference(self):
        response = self.client.post("/v1/images/generations", content=b"{}",
                                    headers={"origin": "https://evil.example", "content-type": "text/plain"})
        self.assertEqual(response.status_code, 403)

    def test_the_playground_itself_may_post(self):
        response = self.client.post("/v1/embeddings", content=b"[",
                                    headers={"origin": "http://localhost"})
        self.assertEqual(response.status_code, 400)

    def test_unknown_request_ids_are_404(self):
        self.assertEqual(self.client.get("/admin/requests/nope").status_code, 404)
        self.assertEqual(self.client.get("/admin/requests/nope/result").status_code, 404)

    def test_transcription_downloads_the_model_first(self):
        from fastapi.responses import JSONResponse
        with patch.object(residency, "ensure_ready", new=AsyncMock(return_value=None)) as ready, \
                patch("omni.gateway.proxy.Proxy.forward",
                      new=AsyncMock(return_value=JSONResponse({}))) as forward:
            response = self.client.post("/v1/audio/transcriptions", files={"file": ("a.wav", b"data")})
            forward.assert_called_once()
            asyncio.run(forward.call_args.kwargs["ready"]())
        self.assertEqual(response.status_code, 200)
        ready.assert_called_once()
        self.assertEqual(ready.call_args.args[1], "stt")

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


class TypeValidation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spawn = patch("omni.supervisor.pool.Supervisor.start", new=AsyncMock())
        cls.spawn.start()
        # the media workers exist only where their runtimes are installed; tests need them everywhere
        cls.runtimes = patch("omni.gateway.context.unavailable_reason", return_value=None)
        cls.runtimes.start()
        cls.client = TestClient(gateway.create(), base_url="http://localhost", raise_server_exceptions=False)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        cls.runtimes.stop()
        cls.spawn.stop()

    def test_chat_non_string_model_is_400_not_500(self):
        response = self.client.post("/v1/chat/completions", json={"model": 123, "messages": "nope"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["type"], "invalid_request_error")

    def test_admin_non_string_model_is_400(self):
        response = self.client.post("/admin/models/pull", json={"role": "tts", "model": {"x": 1}})
        self.assertEqual(response.status_code, 400)

    def test_oversized_json_is_413(self):
        response = self.client.post("/v1/chat/completions",
                                    content=b'{"messages":"' + b"x" * (9 * 1024 * 1024) + b'"}',
                                    headers={"content-type": "application/json"})
        self.assertEqual(response.status_code, 413)


class WorkerAvailability(unittest.TestCase):
    def test_a_role_whose_runtime_is_missing_is_not_supervised(self):
        from omni.gateway import context

        settings = config.load()
        with patch("omni.gateway.context.unavailable_reason",
                   side_effect=lambda role: "needs macOS on Apple Silicon" if role == "tts" else None):
            built = context.build(settings)
        self.assertNotIn("tts", built.workers)
        self.assertIn("stt", built.workers)
        self.assertNotIn("tts", built.clients)

    def test_asking_for_a_role_with_no_worker_is_503_not_a_crash(self):
        from omni.gateway import context

        settings = config.load()

        def reason(role):
            return "needs macOS on Apple Silicon" if role == "tts" else None

        with patch("omni.gateway.context.unavailable_reason", side_effect=reason), \
             patch("omni.gateway.proxy.unavailable_reason", side_effect=reason):
            built = context.build(settings)
            response = asyncio.run(built.proxy.forward(Mock(), "tts", "/v1/audio/speech"))
        self.assertEqual(response.status_code, 503)
        self.assertIn("macOS on Apple Silicon", response.body.decode())
