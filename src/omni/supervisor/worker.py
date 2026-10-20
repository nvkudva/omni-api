"""One supervised child: its command, how to reach it, and its health so far."""
import asyncio
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import psutil

from omni import config


@dataclass
class Worker:
    name: str
    # The argv template. A `{uds}` token, if present, is replaced with this generation's socket path.
    argv: list[str]
    client: httpx.AsyncClient
    health_path: str
    # The stable path clients connect to; spawn republishes it as a symlink to the current generation.
    uds_link: Path | None = None
    # The socket this generation bound, and the argv it was actually started with. Both are set by spawn.
    uds: Path | None = None
    command: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    timeout: float = 2.0
    process: subprocess.Popen | None = None
    ps: psutil.Process | None = None
    status: str = "stopped"
    restarts: int = 0
    failed_probes: int = 0
    ready_since: float = 0.0
    next_start: float = 0.0
    started_at: float = 0.0
    held: bool = False
    # set when a request could not reach a worker that health checks still called ready
    restart_requested: bool = False
    # the worker's own report: which model it serves, and whether that model is in memory
    model: str = ""
    loaded: bool = False
    # serialises spawn/terminate against the watch loop and against admin actions
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def pidfile(self) -> Path:
        return config.RUN_DIR / f"{self.name}.pid"
