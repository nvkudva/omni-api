"""What stands in the way of a load, and the loop that keeps pinned roles resident.

The distinction under test is the one that produced a confirmation in front of every load: advice
about the machine (swap, CPU, load) must never gate a load, while something about *this* model
(not installed, too big for the disk) must.
"""
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

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
        self.assertEqual(load.call_args.kwargs["keep_alive"], residency.OLLAMA_KEEP_ALIVE)

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
        self.assertIn("stt", result)

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


class KeepWarmLoop(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.pins = Path(self.directory.name) / "pins.json"
        patcher = patch.object(config, "PINS", self.pins)
        patcher.start()
        self.addCleanup(patcher.stop)

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
