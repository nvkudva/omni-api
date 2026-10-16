"""Worker apps answer health and reject bad requests without ever loading a model.

The heavy runtimes (torch, mlx, parakeet) are imported lazily inside each worker, so importing the
apps and calling their validation paths here does not load a model.
"""
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from omni.workers import image, stt, tts, video
from omni.workers.runtime import Lazy


class LazyModel(unittest.TestCase):
    def test_loads_on_first_use_and_drops_on_unload(self):
        dropped = []
        lazy = Lazy(lambda: 42, idle_seconds=10_000, after_unload=lambda: dropped.append(True))
        self.assertFalse(lazy.loaded)
        with lazy.use() as model:
            self.assertEqual(model, 42)
        self.assertTrue(lazy.loaded)
        lazy.load()
        self.assertTrue(lazy.loaded)
        lazy.unload()
        self.assertFalse(lazy.loaded)
        self.assertEqual(dropped, [True])
        # dropping an already dropped model is a no-op
        lazy.unload()
        self.assertEqual(dropped, [True])


class WorkerApps(unittest.TestCase):
    def test_apps_are_fastapi_and_expose_health(self):
        for module in (stt, tts, image, video):
            self.assertIsInstance(module.app, FastAPI, module.__name__)
            paths = {route.path for route in module.app.routes}
            self.assertIn("/health", paths, module.__name__)
            self.assertIn("/load", paths, module.__name__)

    def test_health_does_not_load_the_model(self):
        for module in (stt, tts, image, video):
            with self.subTest(module=module.__name__), TestClient(module.app) as client:
                response = client.get("/health")
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.json()["ok"])
                self.assertFalse(module.model.loaded, module.__name__)


class WorkerValidation(unittest.TestCase):
    def setUp(self):
        self.image = TestClient(image.app)
        self.tts = TestClient(tts.app)
        self.stt = TestClient(stt.app)
        self.video = TestClient(video.app)

    def tearDown(self):
        for client in (self.image, self.tts, self.stt, self.video):
            client.close()

    def test_image_rejects_a_foreign_model(self):
        response = self.image.post("/v1/images/generations", json={"prompt": "a cat", "model": "other"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.json())

    def test_image_rejects_an_empty_prompt(self):
        response = self.image.post("/v1/images/generations", json={"prompt": ""})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["type"], "invalid_request_error")

    def test_tts_rejects_an_unknown_voice(self):
        response = self.tts.post("/v1/audio/speech", json={"input": "hi", "voice": "not-a-voice"})
        self.assertEqual(response.status_code, 422)
        self.assertIn("unknown voice", response.json()["error"]["message"])

    def test_tts_lists_voices(self):
        body = self.tts.get("/v1/audio/voices").json()
        self.assertIn(body["default"], body["voices"])

    def test_stt_rejects_an_empty_file(self):
        response = self.stt.post("/v1/audio/transcriptions",
                                 files={"file": ("a.wav", b"", "audio/wav")})
        self.assertEqual(response.status_code, 400)
        self.assertIn("empty", response.json()["error"]["message"])

    def test_stt_rejects_an_unknown_format(self):
        response = self.stt.post("/v1/audio/transcriptions",
                                 files={"file": ("a.wav", b"data", "audio/wav")},
                                 data={"response_format": "xml"})
        self.assertEqual(response.status_code, 400)

    def test_video_rejects_a_bad_frame(self):
        response = self.video.post("/v1/videos", json={"prompt": "x", "image": "!!not base64!!"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("base64", response.json()["error"]["message"])

    def test_video_unknown_job(self):
        self.assertEqual(self.video.get("/v1/videos/video_0000000000000000").status_code, 404)


if __name__ == "__main__":
    unittest.main()
