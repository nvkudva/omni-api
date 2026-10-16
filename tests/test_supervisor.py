"""Process supervision helpers: verifying a pid before signalling, and leftover cleanup."""
import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from omni import config, supervisor
from omni.supervisor.worker import Worker


class IsOurs(unittest.TestCase):
    def test_our_own_process_is_recognised_by_its_command_line(self):
        self.assertTrue(supervisor.is_ours(os.getpid(), ["python"]))

    def test_an_unknown_token_is_not(self):
        self.assertFalse(supervisor.is_ours(os.getpid(), ["definitely-not-on-this-cmdline-xyz"]))

    def test_empty_argv_is_never_ours(self):
        self.assertFalse(supervisor.is_ours(os.getpid(), ["", ""]))

    def test_a_dead_pid_has_no_command_line(self):
        self.assertEqual(supervisor.command_line(-1), "")


class KillLeftovers(unittest.TestCase):
    def test_empty_run_dir_stops_nothing(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(config, "RUN_DIR", Path(directory)):
            self.assertEqual(supervisor.kill_leftovers(), [])

    def test_unreadable_pidfile_is_removed_without_signalling(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            stray = run / "ghost.pid"
            stray.write_text("not json and not a pid")
            with patch.object(config, "RUN_DIR", run):
                self.assertEqual(supervisor.kill_leftovers(), [])
            self.assertFalse(stray.exists())


class WorkerAndStatus(unittest.TestCase):
    def test_pidfile_path_follows_the_name(self):
        worker = Worker(name="image", argv=["x"], client=None, health_path="/health")
        self.assertEqual(worker.pidfile, config.RUN_DIR / "image.pid")
        self.assertEqual(worker.status, "stopped")
        self.assertFalse(worker.held)

    def test_status_of_a_never_started_worker(self):
        client = httpx.AsyncClient()
        try:
            pool = supervisor.Supervisor([Worker(name="tts", argv=["x"], client=client, health_path="/health")])
            status = pool.status()["tts"]
            self.assertEqual(status["status"], "stopped")
            self.assertIsNone(status["pid"])
            self.assertEqual(status["rss_bytes"], 0)
            self.assertFalse(status["held"])
        finally:
            asyncio.run(client.aclose())


if __name__ == "__main__":
    unittest.main()
