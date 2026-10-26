"""The streaming reverse proxy: one httpx client per role, capped bodies, and upstream errors
rewritten into the same OpenAI shape the rest of omni uses. A request naming an execution id
(`X-Omni-Exec`) is retained: the upstream is read to the end in its own task even when the
client goes away, and the full response stays retrievable under /admin/requests."""
import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

from omni import config
from omni.errors import error_response
from omni.gateway import executions
from omni.gateway.http import body_chunks
from omni.registry import unavailable_reason
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
ERROR_BODY_MAX_BYTES = 1024 * 1024


def timeout() -> httpx.Timeout:
    """Finite reads: a stalled upstream must not hold a gateway request forever. 300s covers slow
    first-token and media gaps; the supervisor also kills hung workers, closing their sockets."""
    return httpx.Timeout(connect=5.0, read=300.0, write=60.0, pool=5.0)


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
        async for chunk in body_chunks(request):
            total += len(chunk)
            if total > max_bytes:
                raise HTTPException(413, f"body: larger than {limit}")
            chunks.append(chunk)
        return b"".join(chunks)

    async def forward(self, request: Request, role: str, path: str, body: bytes | None = None,
                      max_bytes: int = DEFAULT_MAX_BYTES,
                      ready: Callable[[], Awaitable[Response | None]] | None = None) -> Response:
        """Proxy one request. `ready` (downloading or loading the model) runs only when the request
        will actually run, never for a replay; a refusal it returns is the answer."""
        if role not in self.clients:
            return error_response(503, unavailable_reason(role) or f"{role} is not available")
        content = await self.read_body(request, max_bytes) if body is None else body
        if len(content) > max_bytes:
            raise HTTPException(413, f"body: larger than {max_bytes // 1024 // 1024} MB")
        exec_id = executions.valid_id(request.headers.get(executions.EXEC_HEADER))
        if exec_id is not None:
            answered = executions.claim(exec_id, role, path, executions.fingerprint(content))
            if answered is not None:
                return answered
        refused = await self._prepare(exec_id, role, path, ready)
        if refused is not None:
            return refused
        if role in self.workers:
            self.workers[role].last_used = time.monotonic()
        headers = {name: value for name, value in request.headers.items() if name in FORWARDED_REQUEST_HEADERS}
        upstream_request = self.clients[role].build_request(request.method, path, content=content, headers=headers)
        # a non-streaming upstream does all its work before the headers, so Stop cancels this task
        sending = asyncio.create_task(self.clients[role].send(upstream_request, stream=True))
        if exec_id is not None:
            executions.TASKS[exec_id] = sending
        try:
            upstream = await sending
        except asyncio.CancelledError:
            if exec_id is None:
                raise
            entry = executions.cancelled(exec_id, role, path)
            if asyncio.current_task().cancelling():
                raise
            return executions.http_response(entry)
        except httpx.TimeoutException:
            # slow, not dead: restarting would kill the very work that is running long
            return self._settle(exec_id, role, path,
                                error_response(504, f"{role} did not answer in time", "timeout"))
        except httpx.TransportError:
            if self.pool is not None:
                self.pool.note_unreachable(role)
            return self._settle(exec_id, role, path,
                                error_response(503, self.unavailable(role), "unavailable"))
        except Exception as failure:
            if exec_id is not None:
                self._settle(exec_id, role, path,
                             error_response(502, f"{role}: {failure}", "upstream_error"))
            raise
        if upstream.status_code >= 400:
            return self._settle(exec_id, role, path, await self._read_error(role, upstream))
        response_headers = {name: value for name, value in upstream.headers.items()
                           if name in FORWARDED_RESPONSE_HEADERS}
        if exec_id is None:
            return StreamingResponse(
                upstream.aiter_raw(),
                status_code=upstream.status_code,
                headers=response_headers,
                background=BackgroundTask(upstream.aclose),
            )
        return self._forward_retained(exec_id, role, path, upstream, response_headers)

    async def _prepare(self, exec_id: str | None, role: str, path: str,
                       ready: Callable[[], Awaitable[Response | None]] | None) -> Response | None:
        """Run `ready` for a request about to run; its refusal, recorded under the id, or None.

        `ready` runs as the id's task, so Stop works during a download or a cold load as well.
        """
        if ready is None:
            return None
        waiting = asyncio.create_task(ready())
        if exec_id is not None:
            executions.TASKS[exec_id] = waiting
        try:
            refused = await waiting
        except asyncio.CancelledError:
            if exec_id is None:
                raise
            entry = executions.cancelled(exec_id, role, path)
            if asyncio.current_task().cancelling():
                raise
            return executions.http_response(entry)
        except BaseException:
            if exec_id is not None:
                executions.cancelled(exec_id, role, path)
            raise
        if refused is None:
            return None
        return self._settle(exec_id, role, path, refused)

    @staticmethod
    def _settle(exec_id: str | None, role: str, path: str, response: Response) -> Response:
        """Answer a request that ended before streaming, remembering it under its id."""
        if exec_id is not None:
            executions.record_response(exec_id, role, path, response)
        return response

    async def _read_error(self, role: str, upstream: httpx.Response) -> JSONResponse:
        """An upstream error body (capped) in the OpenAI shape; a broken read is a 502."""
        capped = bytearray()
        try:
            async for chunk in upstream.aiter_raw():
                capped.extend(chunk[: max(0, ERROR_BODY_MAX_BYTES - len(capped))])
                if len(capped) >= ERROR_BODY_MAX_BYTES:
                    break
        except httpx.HTTPError as failure:
            return error_response(502, f"{role} failed while answering {upstream.status_code}: {failure}",
                                  "upstream_error")
        finally:
            await upstream.aclose()
        return self._upstream_error(role, upstream, bytes(capped))

    def _forward_retained(self, exec_id: str, role: str, path: str, upstream,
                          response_headers: dict) -> StreamingResponse:
        """Stream live while retaining: the pump owns the upstream, the client only watches.

        Closing the tab, navigating away or closing the browser cancels the live generator
        below, never the pump: it reads the upstream to the end and remembers the response.
        """
        content_type = upstream.headers.get("content-type", "application/octet-stream")
        sink = executions.begin(exec_id, role, path, upstream.status_code, content_type, upstream,
                                response_headers)
        # the pump may stop short (hard cap, upstream error), so a declared length could be a lie
        response_headers = {name: value for name, value in response_headers.items()
                            if name != "content-length"}

        async def finish_upstream() -> None:
            task = executions.TASKS.get(exec_id)
            if task is not None:
                try:
                    await task
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001, S110 - the pump already recorded its own outcome
                    pass

        async def live():
            try:
                async for chunk in sink.live_chunks():
                    yield chunk
            finally:
                sink.detach()

        return StreamingResponse(
            live(),
            status_code=upstream.status_code,
            headers=response_headers,
            background=BackgroundTask(finish_upstream),
        )

    def _upstream_error(self, role: str, upstream: httpx.Response, raw: bytes) -> JSONResponse:
        text = raw.decode(errors="replace")
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
