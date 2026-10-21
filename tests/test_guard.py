"""The guard stops its command when the process that started it dies, however that process died."""
import os
import signal
import subprocess
import sys
import textwrap
import time
import unittest

import psutil

from omni.supervisor import guard

STARTER = textwrap.dedent("""
    import subprocess, sys, time
    from omni.supervisor import guard
    process = subprocess.Popen(guard.command(["sleep", "60"]), start_new_session=True)
    print(process.pid, flush=True)
    time.sleep(60)
""")


def wait_until_gone(pid: int, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE:
            return True
        time.sleep(0.1)
    return False


class Guard(unittest.TestCase):
    def test_the_command_dies_with_a_starter_that_was_killed(self):
        starter = subprocess.Popen([sys.executable, "-c", STARTER], stdout=subprocess.PIPE, text=True)
        self.addCleanup(starter.wait)
        self.addCleanup(starter.stdout.close)
        guard_pid = int(starter.stdout.readline())
        deadline = time.monotonic() + 5
        sleepers = []
        while not sleepers and time.monotonic() < deadline:
            sleepers = psutil.Process(guard_pid).children()
            time.sleep(0.1)
        self.assertTrue(sleepers, "the guard never started its command")
        sleeper_pid = sleepers[0].pid
        starter.kill()
        self.assertTrue(wait_until_gone(sleeper_pid, 10), "the command outlived its starter")
        self.assertTrue(wait_until_gone(guard_pid, 10), "the guard outlived its starter")

    def test_the_guard_exits_with_the_exit_code_of_its_command(self):
        result = subprocess.run(guard.command([sys.executable, "-c", "raise SystemExit(3)"]), check=False)
        self.assertEqual(result.returncode, 3)

    def test_sigterm_stops_the_command_and_the_guard(self):
        process = subprocess.Popen(guard.command(["sleep", "60"]), start_new_session=True)
        self.addCleanup(process.wait)
        time.sleep(0.5)
        os.killpg(process.pid, signal.SIGTERM)
        self.assertTrue(wait_until_gone(process.pid, 10))


if __name__ == "__main__":
    unittest.main()
