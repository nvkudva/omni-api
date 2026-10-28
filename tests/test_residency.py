"""What stands in the way of a load, and the loop that keeps pinned roles resident.

The distinction under test is the one that produced a confirmation in front of every load: advice
about the machine (swap, CPU, load) must never gate a load, while something about *this* model
(not installed, too big for the disk) must.
"""
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from omni import config
from omni.gateway import keepwarm, residency


class FakeResponse:
    def __init__(self, body):
        self._body = body

    def json(self):
        return self._body


class FakeClient:
    """A stand-in for an httpx client: every call answers, with no models in memory."""

    async def get(self, path, **kwargs):
        return FakeResponse({})

    async def post(self, path, **kwargs):
        return FakeResponse({})


class FakeContext:
    """Enough of a Context for the residency helpers: clients, workers, settings."""

    def __init__(self, workers=None, settings=None):
        self.clients = {role: FakeClient() for role in config.ROLES}
        self.workers = workers or {}
        self.settings = settings or {role: {"model": f"{role}-model"} for role in config.ROLES}
        self.settings.setdefault("gateway", {"idle_unload_minutes": 10})


def ready_worker(name, loaded=False, busy=0, last_used=0.0):
    return SimpleNamespace(name=name, status="ready", loaded=loaded, busy=busy, last_used=last_used)


class MakeRoom(unittest.TestCase):
    """A cold worker loads before its request, and only once there is RAM for it."""

    def make_room(self, ctx, shortfalls, pins=()):
        installed = [{"name": "image-model", "size_bytes": 10}]
        with patch.object(residency, "installed", AsyncMock(return_value=installed)), \
                patch.object(residency.registry, "ram_shortfall", side_effect=shortfalls), \
                patch.object(residency.config, "load_pins", return_value=list(pins)), \
                patch.object(residency, "_free_ollama", AsyncMock(return_value=None)), \
                patch.object(residency, "set_residency", AsyncMock()) as set_residency:
            refused = asyncio.run(residency.make_room(ctx, "image", "image-model"))
        return refused, set_residency

    def test_a_cold_model_that_fits_is_loaded_first(self):
        ctx = FakeContext(workers={"image": ready_worker("image")})
        refused, set_residency = self.make_room(ctx, [None])
        self.assertIsNone(refused)
        set_residency.assert_awaited_once_with(ctx, "image", "image-model", True)
        self.assertTrue(ctx.workers["image"].loaded)

    def test_an_idle_role_is_unloaded_to_make_room(self):
        ctx = FakeContext(workers={"image": ready_worker("image"), "video": ready_worker("video", loaded=True)})
        refused, set_residency = self.make_room(ctx, ["short", None])
        self.assertIsNone(refused)
        set_residency.assert_any_await(ctx, "video", "video-model", False)
        self.assertFalse(ctx.workers["video"].loaded)

    def test_busy_and_pinned_roles_are_kept_and_the_request_is_refused(self):
        ctx = FakeContext(workers={"image": ready_worker("image"),
                                   "video": ready_worker("video", loaded=True, busy=1),
                                   "tts": ready_worker("tts", loaded=True)})
        refused, set_residency = self.make_room(ctx, ["short", "short"], pins=["tts"])
        self.assertEqual(refused.status_code, 503)
        set_residency.assert_not_awaited()

    def test_a_loaded_worker_needs_no_gate(self):
        ctx = FakeContext(workers={"image": ready_worker("image", loaded=True)})
        refused, set_residency = self.make_room(ctx, [])
        self.assertIsNone(refused)
        set_residency.assert_not_awaited()


class FreeOllama(unittest.TestCase):
    def free(self, resident, shortfalls, pinned=()):
        ctx = FakeContext()
        with patch.object(residency, "ollama_resident", AsyncMock(return_value=resident)), \
                patch.object(residency.registry, "ram_shortfall", side_effect=shortfalls), \
                patch.object(residency.registry, "ollama_unload", AsyncMock()) as unload:
            left = asyncio.run(residency._free_ollama(ctx, "image", 10, set(pinned)))
        return left, unload

    def test_an_unpinned_ollama_model_is_released_for_a_cold_worker(self):
        left, unload = self.free({"chat-model"}, ["short", None])
        self.assertIsNone(left)
        unload.assert_awaited_once()
        self.assertEqual(unload.call_args.args[1:], ("chat", "chat-model"))

    def test_a_pinned_ollama_model_is_kept(self):
        left, unload = self.free({"chat-model"}, ["short"], pinned=["chat"])
        self.assertEqual(left, "short")
        unload.assert_not_awaited()


class Plan(unittest.TestCase):
    def plan(self, role, model, installed):
        ctx = FakeContext()
        with patch.object(residency, "installed", AsyncMock(return_value=installed)):
            return asyncio.run(residency.plan(ctx, role, model))

    def test_an_installed_model_reports_only_its_own_fit(self):
        plan = self.plan("stt", "mlx/parakeet", [{"name": "mlx/parakeet", "size_bytes": 10}])
        self.assertIsNone(plan.fatal)
        self.assertEqual(plan.blockers, [])
        # machine-wide advice is still carried, it just does not gate anything
        self.assertIsInstance(plan.advisories, list)

    def test_an_ollama_model_that_is_not_installed_cannot_be_forced(self):
        plan = self.plan("chat", "qwen2.5:3b", [])
        self.assertIsNotNone(plan.fatal)
        self.assertIn("not installed", plan.fatal)

    def test_a_worker_model_that_is_not_downloaded_is_forceable(self):
        plan = self.plan("stt", "mlx/parakeet", [])
        self.assertIsNone(plan.fatal)
        self.assertEqual(len(plan.blockers), 1)
        self.assertIn("downloads it first", plan.blockers[0])

    def test_a_model_bigger_than_the_disk_is_a_blocker(self):
        huge = {"name": "mlx/parakeet", "size_bytes": 10**18}
        plan = self.plan("stt", "mlx/parakeet", [huge])
        self.assertIsNone(plan.fatal)
        self.assertTrue(any("disk" in blocker for blocker in plan.blockers))


class SetResidency(unittest.TestCase):
    def test_an_ollama_pin_is_held_with_keep_alive_forever(self):
        ctx = FakeContext()
        with patch.object(residency.registry, "ollama_load", AsyncMock()) as load:
            asyncio.run(residency.set_residency(ctx, "chat", "qwen2.5:3b", True, keep_warm=True))
        self.assertEqual(load.call_args.kwargs["keep_alive"], residency.OLLAMA_KEEP_WARM)

    def test_a_plain_ollama_load_uses_the_ordinary_keep_alive(self):
        ctx = FakeContext()
        with patch.object(residency.registry, "ollama_load", AsyncMock()) as load:
            asyncio.run(residency.set_residency(ctx, "chat", "qwen2.5:3b", True))
        self.assertEqual(load.call_args.kwargs["keep_alive"], "10m")

    def test_a_worker_load_downloads_first_when_nothing_is_on_disk(self):
        ctx = FakeContext()

        class Loaded:
            def raise_for_status(self):
                pass

            def json(self):
                return {"loaded": True}

        class Client:
            async def post(self, path, **kwargs):
                return Loaded()

        ctx.clients["stt"] = Client()
        with patch.object(residency, "ensure_downloaded", AsyncMock()) as download:
            result = asyncio.run(residency.set_residency(ctx, "stt", "mlx/parakeet", True))
        download.assert_called_once()
        self.assertTrue(result)

    def test_ensure_downloaded_skips_the_download_when_already_on_disk(self):
        ctx = FakeContext()
        installed = [{"name": "mlx/parakeet", "size_bytes": 10}]
        with patch.object(residency, "installed", AsyncMock(return_value=installed)), \
                patch.object(residency.pulling, "ensure_started") as start:
            asyncio.run(residency.ensure_downloaded(ctx, "stt", "mlx/parakeet"))
        start.assert_not_called()

    def test_a_failed_load_download_surfaces_as_an_error(self):
        ctx = FakeContext()
        with patch.object(residency, "ensure_downloaded", AsyncMock(side_effect=OSError("gated"))), \
                self.assertRaises(OSError):
            asyncio.run(residency.set_residency(ctx, "stt", "mlx/parakeet", True))

    def test_ensure_ready_reports_a_failed_first_download(self):
        ctx = FakeContext()
        with patch.object(residency, "ensure_downloaded", AsyncMock(side_effect=OSError("gated"))):
            result = asyncio.run(residency.ensure_ready(ctx, "stt"))
        self.assertEqual(result.status_code, 502)
        self.assertIn(b"stt", result.body)

    def test_a_first_inference_download_is_visible_while_it_runs(self):
        from omni.registry import pulling

        pulling.ACTIVE.clear()
        pulling.DONE.clear()
        self.addCleanup(pulling.ACTIVE.clear)
        self.addCleanup(pulling.DONE.clear)
        ctx = FakeContext()
        started = asyncio.Event()
        release = asyncio.Event()

        async def events(client, role, model):
            pulling.track(role, model, 100, 10)
            started.set()
            await release.wait()
            yield {"status": "downloading", "total": 100, "completed": 100}
            yield {"status": "success"}

        async def run():
            task = asyncio.create_task(residency.ensure_downloaded(ctx, "stt", "mlx/parakeet"))
            await asyncio.wait_for(started.wait(), timeout=5)
            entries = pulling.snapshot()
            release.set()
            await task
            return entries

        with patch.object(residency, "installed", AsyncMock(return_value=[])), \
                patch.object(residency.pulling, "pull", events):
            entries = asyncio.run(run())
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["role"], "stt")
        self.assertEqual(entries[0]["completed"], 10)


class UnloadOllamaModels(unittest.TestCase):
    """Stopping must hand back the models omni loaded into an Ollama it does not own."""

    def setUp(self):
        residency._loaded_ollama.clear()
        self.addCleanup(residency._loaded_ollama.clear)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        patcher = patch.object(config, "PINS", Path(directory.name) / "pins.json")
        patcher.start()
        self.addCleanup(patcher.stop)

    def load(self, ctx, role, model):
        with patch.object(residency.registry, "ollama_load", AsyncMock()):
            asyncio.run(residency.set_residency(ctx, role, model, True))

    def test_unloads_a_model_this_gateway_loaded(self):
        ctx = FakeContext()
        with (
            patch.object(residency, "ollama_resident", AsyncMock(return_value={"qwen2.5:3b"})),
            patch.object(residency.registry, "ollama_unload", AsyncMock()) as unload,
        ):
            self.load(ctx, "chat", "qwen2.5:3b")
            self.assertEqual(asyncio.run(residency.unload_ollama_models(ctx)), ["qwen2.5:3b"])
        unload.assert_awaited_once_with(ctx.clients["chat"], "chat", "qwen2.5:3b")

    def test_leaves_a_model_this_gateway_did_not_load(self):
        ctx = FakeContext()
        with (
            patch.object(residency, "ollama_resident", AsyncMock(return_value={"someone-elses:7b"})),
            patch.object(residency.registry, "ollama_unload", AsyncMock()) as unload,
        ):
            self.load(ctx, "chat", "qwen2.5:3b")
            self.assertEqual(asyncio.run(residency.unload_ollama_models(ctx)), [])
        unload.assert_not_called()

    def test_unloads_a_pinned_model_an_earlier_gateway_left_held(self):
        ctx = FakeContext()
        config.save_pins(["chat"])
        with (
            patch.object(residency, "ollama_resident", AsyncMock(return_value={"chat-model"})),
            patch.object(residency.registry, "ollama_unload", AsyncMock()) as unload,
        ):
            self.assertEqual(asyncio.run(residency.unload_ollama_models(ctx)), ["chat-model"])
        unload.assert_called_once()

    def test_a_failed_unload_does_not_break_the_shutdown(self):
        ctx = FakeContext()
        with (
            patch.object(residency, "ollama_resident", AsyncMock(return_value={"qwen2.5:3b"})),
            patch.object(residency.registry, "ollama_unload", AsyncMock(side_effect=httpx.ConnectError("gone"))),
        ):
            self.load(ctx, "chat", "qwen2.5:3b")
            self.assertEqual(asyncio.run(residency.unload_ollama_models(ctx)), [])


class KeepWarmLoop(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.pins = Path(self.directory.name) / "pins.json"
        patcher = patch.object(config, "PINS", self.pins)
        patcher.start()
        self.addCleanup(patcher.stop)
        config.save_pins([])

    def loop(self, workers=None):
        ctx = FakeContext(workers=workers or {})
        ctx.settings["stt"]["model"] = "mlx/parakeet"
        return keepwarm.KeepWarm(ctx)

    def test_nothing_pinned_does_no_work(self):
        with patch.object(residency, "set_residency", AsyncMock()) as load:
            self.assertEqual(asyncio.run(self.loop().refresh()), [])
        load.assert_not_called()

    def test_a_pinned_worker_is_re_loaded(self):
        config.save_pins(["stt"])
        with patch.object(residency, "set_residency", AsyncMock(return_value=True)) as load:
            self.assertEqual(asyncio.run(self.loop().refresh()), ["stt"])
        self.assertEqual(load.call_args.args[1], "stt")

    def test_a_pinned_worker_already_holding_its_model_is_left_alone(self):
        config.save_pins(["stt"])
        with patch.object(residency, "set_residency", AsyncMock()) as load:
            self.assertEqual(asyncio.run(self.loop({"stt": ready_worker("stt", loaded=True)}).refresh()), [])
        load.assert_not_called()

    def test_an_idle_unpinned_worker_is_unloaded(self):
        worker = ready_worker("tts", loaded=True, last_used=-1e9)
        with patch.object(residency, "set_residency", AsyncMock()) as unload:
            asyncio.run(self.loop({"tts": worker}).refresh())
        unload.assert_awaited_once()
        self.assertFalse(unload.call_args.args[3])
        self.assertFalse(worker.loaded)

    def test_a_recent_busy_or_pinned_worker_keeps_its_model(self):
        config.save_pins(["stt"])
        workers = {"tts": ready_worker("tts", loaded=True, last_used=keepwarm.time.monotonic()),
                   "image": ready_worker("image", loaded=True, busy=1),
                   "stt": ready_worker("stt", loaded=True)}
        with patch.object(residency, "set_residency", AsyncMock()) as unload:
            asyncio.run(self.loop(workers).refresh())
        unload.assert_not_called()

    def test_zero_idle_minutes_never_unloads(self):
        loop = self.loop({"tts": ready_worker("tts", loaded=True)})
        loop.ctx.settings["gateway"]["idle_unload_minutes"] = 0
        with patch.object(residency, "set_residency", AsyncMock()) as unload:
            asyncio.run(loop.refresh())
        unload.assert_not_called()

    def test_a_pinned_worker_that_is_not_up_yet_is_left_to_the_watch_loop(self):
        config.save_pins(["stt"])
        worker = type("W", (), {"status": "starting"})()
        with patch.object(residency, "set_residency", AsyncMock()) as load:
            loop = self.loop({"stt": worker})
            self.assertEqual(asyncio.run(loop.refresh()), [])
            # and the loop knows to try again soon rather than waiting a full refresh interval
            self.assertTrue(loop._pending)
        load.assert_not_called()

    def test_a_pinned_ollama_model_already_held_is_not_re_loaded(self):
        config.save_pins(["chat"])
        with (
            patch.object(residency, "ollama_resident", AsyncMock(return_value={"chat-model"})),
            patch.object(residency, "set_residency", AsyncMock()) as load,
        ):
            self.assertEqual(asyncio.run(self.loop().refresh()), [])
        load.assert_not_called()

    def test_a_pinned_ollama_model_that_vanished_is_loaded_again(self):
        config.save_pins(["chat"])
        with (
            patch.object(residency, "ollama_resident", AsyncMock(return_value=set())),
            patch.object(residency, "set_residency", AsyncMock()) as load,
        ):
            self.assertEqual(asyncio.run(self.loop().refresh()), ["chat"])
        self.assertTrue(load.call_args.kwargs["keep_warm"])


if __name__ == "__main__":
    unittest.main()
