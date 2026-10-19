"""Gated Hugging Face repos surface as a structured accept-terms event, not a raw upstream error."""
import asyncio
import unittest
from unittest.mock import patch

from omni.registry import pulling


class Gated(unittest.TestCase):
    def test_gated_event_carries_model_and_accept_url(self):
        event = pulling.gated_event("dgrauet/ltx-2.5-mlx-q4")
        self.assertEqual(event["code"], "gated")
        self.assertEqual(event["model"], "dgrauet/ltx-2.5-mlx-q4")
        self.assertEqual(event["accept_url"], "https://huggingface.co/dgrauet/ltx-2.5-mlx-q4")
        self.assertIn("terms accepted", event["error"])

    def test_is_gated_matches_hf_wording(self):
        self.assertTrue(pulling.is_gated(OSError("Cannot access gated repo for url https://huggingface.co/x/y")))
        self.assertTrue(pulling.is_gated(OSError("Access is restricted and you are not in the authorized list")))
        self.assertFalse(pulling.is_gated(OSError("network is unreachable")))


class Tracker(unittest.TestCase):
    def setUp(self):
        pulling.ACTIVE.clear()
        pulling.DONE.clear()
        pulling.TASKS.clear()
        self.addCleanup(pulling.ACTIVE.clear)
        self.addCleanup(pulling.DONE.clear)
        self.addCleanup(pulling.TASKS.clear)

    def test_track_and_snapshot_report_an_in_flight_download(self):
        pulling.track("stt", "mlx/parakeet", 100, 25)
        entries = pulling.snapshot()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["role"], "stt")
        self.assertEqual(entries[0]["model"], "mlx/parakeet")
        self.assertEqual(entries[0]["total"], 100)
        self.assertEqual(entries[0]["completed"], 25)

    def test_finish_drops_the_download(self):
        pulling.track("stt", "mlx/parakeet", 100, 25)
        pulling.finish("mlx/parakeet")
        self.assertEqual(pulling.snapshot(), [])

    def test_a_pull_clears_its_tracker_entry_when_done(self):
        async def events():
            return [{"status": "success"}]

        async def fake_hf(role, model):
            pulling.track(role, model, 10, 10)
            for event in await events():
                yield event

        async def run():
            async for _ in pulling.pull(None, "stt", "mlx/parakeet"):
                pass

        with patch.object(pulling, "_hf_pull", fake_hf):
            asyncio.run(run())
        self.assertEqual(pulling.snapshot(), [])

    def test_concurrent_starters_share_one_background_download(self):
        runs = []

        async def fake_pull(client, role, model):
            runs.append(model)
            yield {"status": "success"}
            pulling.finish(model)

        async def run():
            first = pulling.ensure_started("stt", "mlx/parakeet", None)
            second = pulling.ensure_started("stt", "mlx/parakeet", None)
            self.assertIs(first, second)
            await asyncio.gather(first, second)

        with patch.object(pulling, "pull", fake_pull):
            asyncio.run(run())
        self.assertEqual(runs, ["mlx/parakeet"])
        self.assertEqual(pulling.outcome("mlx/parakeet")["status"], "success")
        self.assertEqual(pulling.snapshot(), [])

    def test_a_pull_that_raises_still_clears_its_tracker_entry(self):
        async def fake_hf(role, model):
            pulling.track(role, model, 10, 5)
            yield {"status": "downloading"}
            raise ValueError("boom")

        async def run():
            await pulling.ensure_started("stt", "mlx/parakeet", None)

        with patch.object(pulling, "_hf_pull", fake_hf):
            asyncio.run(run())
        self.assertEqual(pulling.snapshot(), [])
        self.assertIn("boom", pulling.outcome("mlx/parakeet")["error"])

    def test_fit_warnings_reach_progress_and_outcome(self):
        async def fake_hf(role, model):
            yield {"warning": "not enough disk"}
            self.assertEqual(pulling.progress(model)["warnings"], ["not enough disk"])
            pulling.track(role, model, 10, 5)
            self.assertEqual(pulling.progress(model)["warnings"], ["not enough disk"])
            yield {"status": "success"}

        async def run():
            await pulling.ensure_started("stt", "mlx/parakeet", None)

        with patch.object(pulling, "_hf_pull", fake_hf):
            asyncio.run(run())
        self.assertEqual(pulling.outcome("mlx/parakeet")["warnings"], ["not enough disk"])

    def test_a_failed_background_download_records_its_error(self):
        async def fake_pull(client, role, model):
            yield pulling.gated_event(model)
            pulling.finish(model)

        async def run():
            await pulling.ensure_started("video", "dgrauet/ltx-2.5-mlx-q4", None)

        with patch.object(pulling, "pull", fake_pull):
            asyncio.run(run())
        terminal = pulling.outcome("dgrauet/ltx-2.5-mlx-q4")
        self.assertEqual(terminal["code"], "gated")
        self.assertIn("accept_url", terminal)
        self.assertEqual([entry["model"] for entry in pulling.recent()],
                         ["dgrauet/ltx-2.5-mlx-q4"])

    def test_cancelling_a_watcher_never_cancels_the_download(self):
        release = asyncio.Event()

        async def fake_pull(client, role, model):
            pulling.track(role, model, 100, 10)
            await release.wait()
            yield {"status": "success"}
            pulling.finish(model)

        async def run():
            task = pulling.ensure_started("stt", "mlx/parakeet", None)

            async def watch():
                await asyncio.shield(task)

            watcher = asyncio.create_task(watch())
            await asyncio.sleep(0)
            watcher.cancel()
            try:
                await watcher
            except asyncio.CancelledError:
                pass
            self.assertFalse(task.done())
            release.set()
            await task

        with patch.object(pulling, "pull", fake_pull):
            asyncio.run(run())
        self.assertEqual(pulling.outcome("mlx/parakeet")["status"], "success")

    def test_a_hf_download_publishes_progress_while_it_runs(self):
        seen = []

        async def run():
            async for event in pulling.pull(None, "stt", "mlx/parakeet"):
                seen.append(event)

        with patch.object(pulling.huggingface, "size", return_value=100), \
                patch.object(pulling.huggingface, "download", return_value="/cache"), \
                patch.object(pulling.huggingface, "cache_dir", return_value="/none"), \
                patch.object(pulling.huggingface, "folder_size", side_effect=[10, 100]), \
                patch.object(pulling, "PROGRESS_SECONDS", 0.01):
            asyncio.run(run())
        self.assertTrue(any(event.get("completed") == 10 for event in seen))
        self.assertEqual(seen[-1], {"status": "success"})


if __name__ == "__main__":
    unittest.main()
