"""Builds the shared state a gateway request needs: clients, workers and the proxy."""
import sys
from dataclasses import dataclass

import httpx

from omni import config, supervisor
from omni.gateway.keepwarm import KeepWarm
from omni.gateway.proxy import Proxy, timeout, uds_client
from omni.registry import PYTHON_WORKERS, unavailable_reason


@dataclass
class Context:
    settings: dict
    clients: dict[str, httpx.AsyncClient]
    workers: dict[str, supervisor.Worker]
    pool: supervisor.Supervisor
    chat_url: str
    proxy: Proxy
    keepwarm: KeepWarm | None = None


def _ollama_running(url: str) -> bool:
    try:
        return httpx.get(f"{url}/api/version", timeout=1.0).status_code == 200
    except httpx.HTTPError:
        return False


def build(settings: dict) -> Context:
    """The children to supervise, a client per role, and where chat goes."""
    clients: dict[str, httpx.AsyncClient] = {}
    workers: list[supervisor.Worker] = []

    # chat and embed share one Ollama, on whatever address this machine configures (OLLAMA_HOST).
    chat_url = config.ollama_url()
    clients["chat"] = httpx.AsyncClient(base_url=chat_url, timeout=timeout())
    if not _ollama_running(chat_url):
        workers.append(supervisor.Worker(
            name="chat",
            argv=["ollama", "serve"],
            client=httpx.AsyncClient(base_url=chat_url, timeout=2.0),
            health_path="/api/version",
        ))

    for role in PYTHON_WORKERS:
        # A worker whose runtime is not on this machine (Linux, Windows, or a base install
        # without the media extra) would only crash-loop; leave it out and let the proxy say why.
        if unavailable_reason(role) is not None:
            continue
        socket = config.socket_path(role)
        # the client always dials the stable link; spawn repoints it at each generation's socket
        clients[role] = uds_client(role)
        workers.append(supervisor.Worker(
            name=role,
            argv=[sys.executable, "-m", "uvicorn", f"omni.workers.{role}:app", "--uds", "{uds}",
                  "--log-level", "warning"],
            client=uds_client(role, timeout_=2.0),
            health_path="/health",
            uds_link=socket,
        ))

    worker_map = {worker.name: worker for worker in workers}
    pool = supervisor.Supervisor(workers)
    ctx = Context(
        settings=settings,
        clients=clients,
        workers=worker_map,
        pool=pool,
        chat_url=chat_url,
        proxy=Proxy(clients, worker_map, pool),
    )
    ctx.keepwarm = KeepWarm(ctx)
    return ctx
