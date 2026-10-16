"""Process supervision helpers: verifying a pid before signalling, and leftover cleanup."""
import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from omni import config, supervisor
from omni.supervisor import process
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
            self.assertEqual(status["model"], "")
            self.assertFalse(status["loaded"])
        finally:
            asyncio.run(client.aclose())

    def test_status_reports_the_workers_model_only_while_it_is_ready(self):
        client = httpx.AsyncClient()
        try:
            worker = Worker(name="stt", argv=["x"], client=client, health_path="/health")
            pool = supervisor.Supervisor([worker])
            worker.model, worker.loaded, worker.status = "parakeet", True, "ready"
            self.assertEqual(pool.status()["stt"]["model"], "parakeet")
            self.assertTrue(pool.status()["stt"]["loaded"])
            # a worker that is restarting never claims to be holding a model
            worker.status = "restarting"
            self.assertEqual(pool.status()["stt"]["model"], "")
            self.assertFalse(pool.status()["stt"]["loaded"])
        finally:
            asyncio.run(client.aclose())


class StubClient:
    def __init__(self, response):
        self._response = response

    async def get(self, path, timeout=None):
        return self._response


class HealthProbe(unittest.TestCase):
    def probe(self, body, status_code=200):
        request = httpx.Request("GET", "http://worker/health")
        worker = Worker(name="stt", argv=["x"], client=StubClient(httpx.Response(status_code, json=body, request=request)),
                        health_path="/health")
        pool = supervisor.Supervisor([worker])
        return worker, asyncio.run(pool._healthy(worker))

    def test_a_healthy_worker_reports_its_model_and_loaded_flag(self):
        worker, ok = self.probe({"ok": True, "model": "parakeet", "loaded": True})
        self.assertTrue(ok)
        self.assertEqual(worker.model, "parakeet")
        self.assertTrue(worker.loaded)

    def test_an_unloaded_worker_clears_the_loaded_flag(self):
        worker, ok = self.probe({"ok": True, "model": "parakeet", "loaded": False})
        self.assertTrue(ok)
        self.assertFalse(worker.loaded)

    def test_a_failed_probe_is_not_a_report(self):
        worker, ok = self.probe({}, status_code=503)
        self.assertFalse(ok)
        self.assertEqual(worker.model, "")
        self.assertFalse(worker.loaded)

    def test_a_body_that_is_not_a_report_still_counts_as_healthy(self):
        request = httpx.Request("GET", "http://worker/health")
        response = httpx.Response(200, text="up", request=request)
        worker = Worker(name="stt", argv=["x"], client=StubClient(response), health_path="/health")
        pool = supervisor.Supervisor([worker])
        self.assertTrue(asyncio.run(pool._healthy(worker)))
        self.assertEqual(worker.model, "")


class SocketGenerations(unittest.TestCase):
    """Each worker generation binds its own socket and repoints the stable link, so a slow exit can
    only ever unlink the socket it bound itself."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.run_dir = Path(self.directory.name)
        for name in ("RUN_DIR", "LOG_DIR"):
            patcher = patch.object(config, name, self.run_dir)
            patcher.start()
            self.addCleanup(patcher.stop)

    def worker(self):
        return Worker(name="stt", argv=["uvicorn", "--uds", "{uds}"], client=None, health_path="/health",
                      uds_link=config.socket_path("stt"))

    def spawn(self, worker):
        with patch.object(process.subprocess, "Popen") as popen:
            popen.return_value.pid = 4242
            process.spawn(worker)
        # the process is gone from here on; only the socket bookkeeping is under test
        worker.process = None

    def test_spawn_binds_a_generation_socket_and_points_the_link_at_it(self):
        worker = self.worker()
        self.spawn(worker)
        self.assertTrue(worker.uds.name.startswith("stt."), worker.uds)
        self.assertIn(str(worker.uds), worker.command)
        self.assertTrue(worker.uds_link.is_symlink())
        self.assertEqual(Path(os.readlink(worker.uds_link)), worker.uds)

    def test_each_generation_gets_its_own_socket(self):
        first, second = self.worker(), self.worker()
        self.spawn(first)
        self.spawn(second)
        self.assertNotEqual(first.uds, second.uds)
        self.assertEqual(Path(os.readlink(second.uds_link)), second.uds)

    def test_a_generation_leaves_a_newer_generations_link_alone(self):
        first, second = self.worker(), self.worker()
        self.spawn(first)
        old_uds = first.uds
        self.spawn(second)
        process.terminate(first)
        self.assertFalse(old_uds.exists())
        self.assertTrue(second.uds_link.is_symlink())
        self.assertEqual(Path(os.readlink(second.uds_link)), second.uds)

    def test_terminate_removes_its_own_socket_and_link(self):
        worker = self.worker()
        self.spawn(worker)
        uds = worker.uds
        process.terminate(worker)
        self.assertFalse(uds.exists())
        self.assertFalse(worker.uds_link.is_symlink())
        self.assertIsNone(worker.uds)

    def test_a_worker_without_a_link_never_touches_the_run_dir(self):
        worker = Worker(name="chat", argv=["ollama", "serve"], client=None, health_path="/api/version")
        self.spawn(worker)
        self.assertIsNone(worker.uds)
        self.assertEqual(sorted(p.name for p in self.run_dir.iterdir() if p.suffix == ".sock"), [])


class StaleSockets(unittest.TestCase):
    def test_startup_removes_a_dangling_link_and_dead_generation_sockets(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            (run / "stt.1.1.sock").write_text("")          # a generation whose worker is gone
            link = run / "stt.sock"
            os.symlink(run / "stt.gone.sock", link)        # a link to a socket that is no longer there
            (run / "stt.pid").write_text('{"pid": 999999, "argv": []}')
            with patch.object(config, "RUN_DIR", run):
                supervisor.kill_leftovers()
            self.assertFalse(link.is_symlink())
            self.assertFalse((run / "stt.1.1.sock").exists())


class UnreachableRequests(unittest.TestCase):
    def test_a_ready_worker_is_asked_to_restart(self):
        worker = Worker(name="stt", argv=["x"], client=None, health_path="/health")
        pool = supervisor.Supervisor([worker])
        worker.status = "ready"
        pool.note_unreachable("stt")
        self.assertTrue(worker.restart_requested)

    def test_a_worker_that_is_not_ready_is_left_to_the_watch_loop(self):
        worker = Worker(name="stt", argv=["x"], client=None, health_path="/health")
        pool = supervisor.Supervisor([worker])
        worker.status = "stopped"
        pool.note_unreachable("stt")
        self.assertFalse(worker.restart_requested)

    def test_an_unknown_role_is_ignored(self):
        pool = supervisor.Supervisor([])
        pool.note_unreachable("nope")


if __name__ == "__main__":
    unittest.main()
