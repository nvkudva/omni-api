"""Run one command and take it down whenever the process that started us is gone.

Workers and downloads run in their own session, so a Ctrl-C or a closing terminal never reaches them.
That also means a supervisor killed without cleanup (SIGKILL, a crash, the OOM killer) would leave them
running with nobody to stop them. This guard sits between the two: it polls its parent and, once that
changes, stops the whole group it leads. It uses only the standard library and is run by path, so it
starts without importing the rest of omni.
"""
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

POLL_SECONDS = 0.2
GRACE_SECONDS = 5.0


def command(argv: list[str]) -> list[str]:
    """`argv`, wrapped so it cannot outlive the process that starts it."""
    return [sys.executable, str(Path(__file__).resolve()), *argv]


def _stop_group(signum: int) -> None:
    """Signal everything in our session; only when we lead it, else just our own child's group."""
    if os.getpgrp() == os.getpid():
        os.killpg(os.getpgrp(), signum)


def run(argv: list[str]) -> int:
    starter = os.getppid()
    child = subprocess.Popen(argv)
    stop_requested = False

    def request_stop(signum, frame) -> None:
        nonlocal stop_requested
        stop_requested = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGHUP, request_stop)
    kill_at = None
    while True:
        exit_code = child.poll()
        if exit_code is not None:
            return exit_code if exit_code >= 0 else 128 - exit_code
        if kill_at is None and (stop_requested or os.getppid() != starter):
            kill_at = time.monotonic() + GRACE_SECONDS
            if os.getpgrp() == os.getpid():
                # our own handlers swallow this; the child and anything it forked get it
                _stop_group(signal.SIGTERM)
            else:
                child.terminate()
        if kill_at is not None and time.monotonic() > kill_at:
            if os.getpgrp() == os.getpid():
                _stop_group(signal.SIGKILL)
            child.kill()
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
