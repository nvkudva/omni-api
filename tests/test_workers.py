"""Worker apps answer health and reject bad requests without ever loading a model.

The heavy runtimes (torch, onnxruntime, onnx-asr) are imported lazily inside each worker, so importing the
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

from omni.workers import decide, image, runtime, stt, tts, video
from omni.workers.runtime import Lazy


class LazyModel(unittest.TestCase):
    def test_loads_on_first_use_and_drops_on_unload(self):
        dropped = []
        lazy = Lazy(lambda: 42, after_unload=lambda: dropped.append(True))
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


class VideoExpiry(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        patcher = patch.object(video.config, "VIDEO_DIR", Path(self.dir.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(video.jobs.clear)

    def run_generate(self, expire):
        def render(pipe, req, width, height, image_path):
            expire()
            return "frames"

        def write_mp4(frames, path):
            Path(path).write_bytes(b"mp4")

        job = {"id": "video_0", "status": "queued", "created_at": 0}
        video.jobs["video_0"] = job
        req = video.schemas.VideoRequest(prompt="x")
        with patch.object(video, "model", Lazy(object)), \
                patch.object(video, "_render", render), patch.object(video, "_write_mp4", write_mp4):
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


class SttTranscription(unittest.TestCase):
    def transcribe(self, response_format):
        from types import SimpleNamespace
        from unittest.mock import patch

        import numpy as np

        class FakeTranscriber:
            def recognize(self, samples, sample_rate):
                return SimpleNamespace(text="Hello there. Bye", tokens=["\u2581Hello", "\u2581there", ".", "\u2581Bye"],
                                       timestamps=[0.1, 0.5, 0.6, 1.2])

        original = stt.model
        stt.model = Lazy(FakeTranscriber)
        try:
            silence = np.zeros(stt.SAMPLE_RATE * 2, dtype=np.float32)
            with patch.object(stt, "_decode", return_value=silence), TestClient(stt.app) as client:
                return client.post("/v1/audio/transcriptions", files={"file": ("a.wav", b"data", "audio/wav")},
                                   data={"response_format": response_format})
        finally:
            stt.model = original

    def test_json_returns_the_text(self):
        response = self.transcribe("json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"text": "Hello there. Bye"})

    def test_verbose_json_splits_sentences_with_times(self):
        body = self.transcribe("verbose_json").json()
        self.assertEqual([(s["start"], s["end"], s["text"]) for s in body["segments"]],
                         [(0.1, 0.6, "Hello there."), (1.2, 1.2, "Bye")])
        self.assertEqual(body["duration"], 1.2)


class TtsChunking(unittest.TestCase):
    def test_sentences_pack_up_to_the_phoneme_limit_and_long_ones_are_cut(self):
        limit = tts.MAX_PHONEMES
        chunks = tts._phoneme_chunks(["a" * 300, "b" * 300, "c" * (limit + 10)])
        self.assertTrue(all(len(chunk) <= limit for chunk in chunks))
        self.assertEqual("".join(chunks).replace(" ", ""), "a" * 300 + "b" * 300 + "c" * (limit + 10))


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
        self.assertEqual(error["accept_url"], f"https://huggingface.co/{video.model_name}")
        self.assertIn("terms accepted", error["message"])
        self.assertNotIn("403 Client Error", error["message"])

    def test_video_other_failures_keep_the_raw_message(self):
        error = video.job_error(ValueError("bad size"))
        self.assertEqual(error, {"message": "ValueError: bad size", "type": "server_error"})


if __name__ == "__main__":
    unittest.main()


class DecideValidation(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(decide.app)
        self.addCleanup(self.client.close)

    def post(self, payload):
        return self.client.post("/v1/systemone", json=payload)

    def test_a_missing_question_map_is_a_400(self):
        response = self.post({"state": "x"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("questions", response.json()["error"]["message"])

    def test_a_missing_state_is_a_400(self):
        response = self.post({"questions": {"q": {"instructions": "?", "criteria": ["A", "B"]}}})
        self.assertEqual(response.status_code, 400)
        self.assertIn("state", response.json()["error"]["message"])

    def test_an_image_that_is_not_one_is_a_400(self):
        response = self.post({"image": "not base64", "questions": {"q": {"instructions": "?", "criteria": ["A", "B"]}}})
        self.assertEqual(response.status_code, 400)
        self.assertIn("base64", response.json()["error"]["message"])

    def test_a_malformed_question_is_a_400(self):
        response = self.post({"state": "x", "questions": {"q": {"type": "mystery", "instructions": "?"}}})
        self.assertEqual(response.status_code, 400)

    def test_another_model_is_refused(self):
        response = self.post({"model": "llama3.2", "state": "x",
                              "questions": {"q": {"instructions": "?", "criteria": ["A", "B"]}}})
        self.assertEqual(response.status_code, 400)
        self.assertIn("this worker serves only", response.json()["error"]["message"])

    def test_the_answer_carries_the_calibrated_fields(self):
        from unittest.mock import patch

        class FakeDecider:
            def decide(self, state_text, rendered, image=None):
                return {"type": "noul", "noul": 0.9, "abstain": False}, 12

        original = decide.model
        decide.model = Lazy(FakeDecider)
        try:
            with patch.object(decide, "executor", decide.executor):
                body = self.post({"state": {"a": 1}, "questions": {"q": {"type": "noul", "instructions": "Is it?"}}}).json()
        finally:
            decide.model = original
        self.assertEqual(body["answers"]["q"]["noul"], 0.9)
        self.assertEqual(body["usage"]["prompt_tokens"], 12)
