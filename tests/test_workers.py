"""Worker apps answer health and reject bad requests without ever loading a model.

The heavy runtimes (torch, mlx, parakeet) are imported lazily inside each worker, so importing the
apps and calling their validation paths here does not load a model.
"""
import asyncio
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from omni.workers import image, runtime, stt, tts, video
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

    def test_zero_idle_seconds_never_unloads(self):
        # 0 is "never": the model stays resident no matter how long it sits unused.
        with patch.object(runtime, "IDLE_CHECK_SECONDS", 0.01):
            lazy = Lazy(lambda: 42, idle_seconds=0)
            lazy.load()
            time.sleep(0.05)
            self.assertTrue(lazy.loaded)

    def test_positive_idle_seconds_drops_after_the_window(self):
        # the control for the test above: with a real window the idle thread does drop the model
        with patch.object(runtime, "IDLE_CHECK_SECONDS", 0.01):
            lazy = Lazy(lambda: 42, idle_seconds=0.01)
            lazy.load()
            time.sleep(0.2)
            self.assertFalse(lazy.loaded)

    def test_requests_past_the_cap_are_refused_not_queued(self):
        executor = runtime.ModelExecutor("test-model", max_pending=1)
        release = threading.Event()

        async def run():
            first = asyncio.ensure_future(executor.run_request(release.wait))
            await asyncio.sleep(0.05)
            with self.assertRaises(HTTPException) as refused:
                await executor.run_request(lambda: None)
            release.set()
            await first
            return refused.exception.status_code

        self.assertEqual(asyncio.run(run()), 429)
        self.assertEqual(executor.pending, 0)

    def test_idle_unload_runs_on_the_loading_thread(self):
        executor = runtime.ModelExecutor("test-model")
        threads = {}

        def load():
            threads["load"] = threading.current_thread()
            return 42

        with patch.object(runtime, "IDLE_CHECK_SECONDS", 0.01):
            lazy = Lazy(load, idle_seconds=0.01, after_unload=lambda: threads.setdefault("drop", threading.current_thread()))
            asyncio.run(executor.run(lazy.load))
            time.sleep(0.3)
        self.assertFalse(lazy.loaded)
        self.assertIs(threads["drop"], threads["load"])


class VideoExpiry(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        patcher = patch.object(video.config, "VIDEO_DIR", Path(self.dir.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(video.jobs.clear)

    def run_generate(self, expire):
        class Pipe:
            def generate_and_save(self, prompt, path, **kwargs):
                Path(path).write_bytes(b"mp4")
                expire()

        job = {"id": "video_0", "status": "queued", "created_at": 0}
        video.jobs["video_0"] = job
        req = video.schemas.VideoRequest(prompt="x")
        with patch.object(video, "model", Lazy(Pipe, idle_seconds=0)):
            video._generate("video_0", req, 64, 64)
        return job

    def test_job_expired_mid_generation_keeps_failed_and_deletes_the_file(self):
        job = self.run_generate(lambda: video.jobs["video_0"].update(status="failed"))
        self.assertEqual(job["status"], "failed")
        self.assertEqual(list(Path(self.dir.name).iterdir()), [])

    def test_job_dropped_mid_generation_deletes_the_file(self):
        job = self.run_generate(lambda: video.jobs.pop("video_0"))
        self.assertNotIn("path", job)
        self.assertEqual(list(Path(self.dir.name).iterdir()), [])

    def test_live_job_completes_and_keeps_the_file(self):
        job = self.run_generate(lambda: None)
        self.assertEqual(job["status"], "completed")
        self.assertTrue(Path(job["path"]).exists())

    def test_startup_purges_only_stale_files(self):
        old, fresh = Path(self.dir.name, "old.mp4"), Path(self.dir.name, "fresh.mp4")
        old.write_bytes(b"x")
        fresh.write_bytes(b"x")
        stale = time.time() - video.JOB_TTL_SECONDS - 10
        os.utime(old, (stale, stale))
        video._purge_stale_files()
        self.assertFalse(old.exists())
        self.assertTrue(fresh.exists())


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


class SttModelThread(unittest.TestCase):
    def test_load_and_transcribe_stay_on_one_thread(self):
        # MLX streams are thread-local: if load and transcribe land on
        # different threads, parakeet raises "no Stream in current thread".
        import threading
        from types import SimpleNamespace

        seen = []

        class FakeTranscriber:
            def transcribe(self, path):
                seen.append(threading.get_ident())
                sentence = SimpleNamespace(start=0.0, end=1.0, text="hello")
                return SimpleNamespace(text="hello", sentences=[sentence])

        def fake_load():
            seen.append(threading.get_ident())
            return FakeTranscriber()

        original = stt.model
        stt.model = Lazy(fake_load, idle_seconds=10_000)
        try:
            with TestClient(stt.app) as client:
                for _ in range(2):
                    response = client.post(
                        "/v1/audio/transcriptions",
                        files={"file": ("a.wav", b"data", "audio/wav")},
                        data={"response_format": "verbose_json"},
                    )
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json()["text"], "hello")
        finally:
            stt.model = original
        self.assertEqual(len(seen), 3)
        self.assertEqual(len(set(seen)), 1, seen)


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

    def test_video_gated_model_fails_with_accept_terms_not_raw_trace(self):
        error = video.job_error(OSError(
            "403 Client Error. Cannot access gated repo for url "
            "https://huggingface.co/baa-ai/LTX-2.3-22B-RAM-12GB-MLX/resolve/main/.gitattributes. "
            "Access to model baa-ai/LTX-2.3-22B-RAM-12GB-MLX is restricted and you are not in the authorized list."))
        self.assertEqual(error["code"], "gated")
        self.assertEqual(error["accept_url"], "https://huggingface.co/baa-ai/LTX-2.3-22B-RAM-12GB-MLX")
        self.assertIn("terms accepted", error["message"])
        self.assertNotIn("403 Client Error", error["message"])

    def test_video_other_failures_keep_the_raw_message(self):
        error = video.job_error(ValueError("bad size"))
        self.assertEqual(error, {"message": "ValueError: bad size", "type": "server_error"})


if __name__ == "__main__":
    unittest.main()
