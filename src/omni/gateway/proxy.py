"""The streaming reverse proxy: one httpx client per role, capped bodies, and upstream errors
rewritten into the same OpenAI shape the rest of omni uses."""
import json
import logging
from typing import TYPE_CHECKING

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

from omni import config
from omni.errors import error_response
from omni.supervisor.worker import Worker

if TYPE_CHECKING:
    from omni.supervisor.pool import Supervisor

log = logging.getLogger("omni.gateway")

FORWARDED_REQUEST_HEADERS = ("content-type", "accept", "range")
FORWARDED_RESPONSE_HEADERS = (
    "content-type", "server-timing", "x-sample-rate",
    "content-range", "accept-ranges", "content-length", "content-disposition", "cache-control",
)
DEFAULT_MAX_BYTES = 8 * 1024 * 1024


def timeout() -> httpx.Timeout:
    """Reads are unbounded: an image or a video streams for minutes without a byte. A worker that stops
    answering is killed by the supervisor, which closes the socket and ends the request."""
    return httpx.Timeout(connect=5.0, read=None, write=60.0, pool=5.0)


def uds_client(role: str, timeout_: httpx.Timeout | float = None) -> httpx.AsyncClient:
    transport = httpx.AsyncHTTPTransport(uds=str(config.socket_path(role)))
    return httpx.AsyncClient(transport=transport, base_url="http://worker", timeout=timeout_ or timeout())


class Proxy:
    def __init__(self, clients: dict[str, httpx.AsyncClient], workers: dict[str, Worker],
                 pool: "Supervisor | None" = None):
        self.clients = clients
        self.workers = workers
        self.pool = pool

    def state(self, role: str) -> str:
        """A human-readable worker state for a 503 message."""
        worker = self.workers.get(role)
        return worker.status if worker else "external"

    def unavailable(self, role: str) -> str:
        """Why a role could not be reached, in words that match what the operator will see next."""
        state = self.state(role)
        if state == "ready":
            return (f"{role} stopped accepting connections ({state}); restarting it — "
                    f"GET /health shows progress")
        return f"{role} is not ready ({state}); GET /health shows progress"

    async def read_body(self, request: Request, max_bytes: int) -> bytes:
        """Buffer the request body, refusing anything bigger than `max_bytes`."""
        limit = f"{max_bytes // 1024 // 1024} MB"
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > max_bytes:
            raise HTTPException(413, f"body: larger than {limit}")
        chunks, total = [], 0
        async for chunk in request.stream():
            total += len(chunk)
            if total > max_bytes:
                raise HTTPException(413, f"body: larger than {limit}")
            chunks.append(chunk)
        return b"".join(chunks)

    async def forward(self, request: Request, role: str, path: str, body: bytes | None = None,
                      max_bytes: int = DEFAULT_MAX_BYTES) -> StreamingResponse | JSONResponse:
        content = await self.read_body(request, max_bytes) if body is None else body
        if len(content) > max_bytes:
            raise HTTPException(413, f"body: larger than {max_bytes // 1024 // 1024} MB")
        headers = {name: value for name, value in request.headers.items() if name in FORWARDED_REQUEST_HEADERS}
        upstream_request = self.clients[role].build_request(request.method, path, content=content, headers=headers)
        try:
            upstream = await self.clients[role].send(upstream_request, stream=True)
        except httpx.TransportError:
            if self.pool is not None:
                self.pool.note_unreachable(role)
            return error_response(503, self.unavailable(role), "unavailable")
        if upstream.status_code >= 400:
            await upstream.aread()
            await upstream.aclose()
            return self._upstream_error(role, upstream)
        response_headers = {name: value for name, value in upstream.headers.items()
                           if name in FORWARDED_RESPONSE_HEADERS}
        return StreamingResponse(
            upstream.aiter_raw(),
            status_code=upstream.status_code,
            headers=response_headers,
            background=BackgroundTask(upstream.aclose),
        )

    def _upstream_error(self, role: str, upstream: httpx.Response) -> JSONResponse:
        text = upstream.text
        try:
            parsed = json.loads(text)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            error = parsed.get("error")
            if isinstance(error, dict) and "message" in error:
                return JSONResponse(parsed, status_code=upstream.status_code)  # already our shape
            if isinstance(error, str):
                return error_response(upstream.status_code, error, "upstream_error")
            if "detail" in parsed:
                detail = parsed["detail"]
                message = detail if isinstance(detail, str) else json.dumps(detail)
                return error_response(upstream.status_code, message, "upstream_error")
        message = text.strip()[:500] or f"{role} returned {upstream.status_code}"
        return error_response(upstream.status_code, message, "upstream_error")
