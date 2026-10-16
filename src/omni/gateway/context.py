"""Builds the shared state a gateway request needs: clients, workers and the proxy."""
import sys
from dataclasses import dataclass

import httpx

from omni import config, supervisor
from omni.gateway.proxy import Proxy, timeout, uds_client
from omni.registry import PYTHON_WORKERS


@dataclass
class Context:
    settings: dict
    clients: dict[str, httpx.AsyncClient]
    workers: dict[str, supervisor.Worker]
    pool: supervisor.Supervisor
    chat_url: str
    proxy: Proxy


def _ollama_running(url: str) -> bool:
    try:
        return httpx.get(f"{url}/api/version", timeout=1.0).status_code == 200
    except httpx.HTTPError:
        return False


def build(settings: dict) -> Context:
    """The children to supervise, a client per role, and where chat goes."""
    clients: dict[str, httpx.AsyncClient] = {}
    workers: list[supervisor.Worker] = []

    # chat, decide and embed share one Ollama, on whatever address this machine configures (OLLAMA_HOST).
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
        socket = config.socket_path(role)
        clients[role] = uds_client(role)
        workers.append(supervisor.Worker(
            name=role,
            argv=[sys.executable, "-m", "uvicorn", f"omni.workers.{role}:app", "--uds", str(socket),
                  "--log-level", "warning"],
            client=uds_client(role, timeout_=2.0),
            health_path="/health",
            uds=socket,
        ))

    worker_map = {worker.name: worker for worker in workers}
    return Context(
        settings=settings,
        clients=clients,
        workers=worker_map,
        pool=supervisor.Supervisor(workers),
        chat_url=chat_url,
        proxy=Proxy(clients, worker_map),
    )
