"""Server-owned inference executions: a retained request runs to completion no matter what.

A client opts in with an `X-Omni-Exec` header carrying an id it made up. The first request
with an id owns the execution: the gateway runs it in its own task, streams the live response
as usual, and keeps reading the upstream to the end even when the browser tab navigates away
or closes. The full response is then retrievable with `GET /admin/requests/{id}/result`, and a
repeated POST with the same id replays it instead of running the model again, unless it failed
or was cancelled, which runs it afresh. A POST with an id that is still running is a 409
pointing at the status endpoint. `DELETE /admin/requests/{id}` cancels a running execution.
"""
import asyncio
import time

from fastapi.responses import Response

from omni.errors import error_response

EXEC_HEADER = "x-omni-exec"
ID_MAX_LENGTH = 128
# retained bodies larger than this are stored truncated; the live stream is unaffected until
# HARD_CAP_BYTES, past which both stop (only pathological single responses get there)
MAX_BODY_BYTES = 24 * 1024 * 1024
HARD_CAP_BYTES = 64 * 1024 * 1024
LIVE_BACKPRESSURE_BYTES = 8 * 1024 * 1024
MAX_DONE = 50
TTL_SECONDS = 1800.0

CANCELLED = "cancelled"

TASKS: dict[str, asyncio.Task] = {}
"""The task doing each execution's work: the upstream send while it waits for headers, then
the background pump, which is owned by this module and never by a watching request."""

ACTIVE: dict[str, dict] = {}
"""Executions still running, by id."""

DONE: dict[str, dict] = {}
"""Finished executions, by id: metadata plus the stored response for replay."""


def valid_id(value) -> str | None:
    """The execution id a request names, or None when it names none or a malformed one."""
    if not isinstance(value, str):
        return None
    if not 1 <= len(value) <= ID_MAX_LENGTH:
        return None
    if all(char.isascii() and (char.isalnum() or char in "-_.") for char in value):
        return value
    return None


def get(exec_id: str) -> dict | None:
    """The execution, running or finished, or None when unknown."""
    entry = ACTIVE.get(exec_id)
    if entry is not None:
        return {**entry, "status": "running"}
    entry = DONE.get(exec_id)
    if entry is not None:
        return {**entry, "status": "done"}
    return None


def _show_running(exec_id: str, role: str, path: str) -> None:
    ACTIVE[exec_id] = {"id": exec_id, "role": role, "path": path, "started_at": time.monotonic(),
                       "bytes": 0, "status_code": None, "content_type": None}


def claim(exec_id: str, role: str, path: str) -> Response | None:
    """Take `exec_id` for a new run, or answer without running.

    Returns None when claimed (the run shows as running from now on; the caller puts the task
    doing the work in TASKS), a 409 when the id is still running, or the stored response when
    it finished cleanly and should replay.
    """
    entry = get(exec_id)
    if entry is not None and entry["status"] == "running":
        return error_response(
            409, f"request {exec_id} is already running; GET /admin/requests/{exec_id} to watch it",
            "conflict")
    if entry is not None and entry.get("error") is None:
        return http_response(entry)
    _show_running(exec_id, role, path)
    return None


def cancel(exec_id: str) -> bool:
    """Stop a running execution's work for good. False when it is not running."""
    task = TASKS.get(exec_id)
    if exec_id not in ACTIVE or task is None or task.done():
        return False
    task.cancel()
    return True


def cancelled(exec_id: str, role: str, path: str) -> dict:
    """Remember that an execution was stopped before it answered."""
    return record(exec_id, role, path, 499, "application/json",
                  error_response(499, f"request {exec_id} was cancelled", CANCELLED).body,
                  error=CANCELLED)


def _meta(entry: dict) -> dict:
    return {key: value for key, value in entry.items() if key != "body"}


def running() -> list[dict]:
    """Executions still in flight, oldest first, without response bodies."""
    return [_meta(entry) for entry in sorted(ACTIVE.values(), key=lambda e: e["started_at"])]


def recent() -> list[dict]:
    """Finished executions still worth reporting, newest first, without response bodies."""
    now = time.monotonic()
    for exec_id in [key for key, entry in DONE.items()
                    if now - entry.get("finished_at", 0) > TTL_SECONDS]:
        del DONE[exec_id]
    return [_meta(entry) for entry in
            sorted(DONE.values(), key=lambda e: e["finished_at"], reverse=True)[:MAX_DONE]]


def _remember(entry: dict) -> None:
    now = time.monotonic()
    ACTIVE.pop(entry["id"], None)
    DONE[entry["id"]] = {**entry, "finished_at": now}
    while len(DONE) > MAX_DONE:
        oldest = min(DONE, key=lambda key: DONE[key]["finished_at"])
        del DONE[oldest]


class Sink:
    """One retained response: the pump feeds it, the live client reads from it, the store keeps it."""

    def __init__(self, exec_id: str, role: str, path: str, status: int, content_type: str) -> None:
        self.exec_id = exec_id
        self.role = role
        self.path = path
        self.status = status
        self.content_type = content_type
        self.started_at = ACTIVE.get(exec_id, {}).get("started_at", time.monotonic())
        self.stream = bytearray()
        self.bytes = 0
        self.live_offset = 0
        self.attached = True
        self.finished = False
        self.pooled = asyncio.Event()
        ACTIVE[exec_id] = self.describe()

    def describe(self) -> dict:
        return {"id": self.exec_id, "role": self.role, "path": self.path,
                "started_at": self.started_at, "bytes": self.bytes,
                "status_code": self.status, "content_type": self.content_type}

    def feed(self, chunk: bytes) -> bool:
        """Append one upstream chunk for the live client. False when past the hard cap."""
        if self.bytes + len(chunk) > HARD_CAP_BYTES:
            return False
        self.stream.extend(chunk)
        self.bytes += len(chunk)
        if self.exec_id in ACTIVE:
            ACTIVE[self.exec_id] = self.describe()
        self.pooled.set()
        return True

    async def wait_for_pressure(self) -> None:
        """Hold the pump back while a slow live client falls behind; a gone client never blocks."""
        while self.attached and self.bytes - self.live_offset > LIVE_BACKPRESSURE_BYTES:
            await asyncio.sleep(0.1)

    def detach(self) -> None:
        """The live client is gone: keep the pump running, stop holding it back."""
        self.attached = False
        if self.exec_id in ACTIVE:
            ACTIVE[self.exec_id] = self.describe()

    async def live_chunks(self):
        """Each upstream chunk in order, ending when the pump finishes."""
        stream = self.stream
        while True:
            while self.live_offset < len(stream):
                end = min(len(stream), self.live_offset + 65536)
                data = bytes(stream[self.live_offset:end])
                self.live_offset = end
                yield data
            if self.finished:
                return
            self.pooled.clear()
            await self.pooled.wait()

    def finish(self, error: str | None = None) -> dict:
        """Close the execution and remember it for replay. Returns the stored entry."""
        self.finished = True
        self.pooled.set()
        body = bytes(self.stream[:MAX_BODY_BYTES])
        entry = {"id": self.exec_id, "role": self.role, "path": self.path,
                 "started_at": self.started_at, "status_code": self.status,
                 "content_type": self.content_type, "size": len(self.stream),
                 "truncated": len(self.stream) > MAX_BODY_BYTES,
                 "body": body, "error": error}
        del self.stream
        TASKS.pop(self.exec_id, None)
        _remember(entry)
        return entry


async def _pump(sink: Sink, upstream) -> None:
    """Drain one upstream to the end whatever the live client does, then remember the result."""
    error = None
    try:
        async for chunk in upstream.aiter_raw():
            if not sink.feed(bytes(chunk)):
                error = f"response larger than {HARD_CAP_BYTES // 1024 // 1024} MB; retained truncated"
                break
            await sink.wait_for_pressure()
    except asyncio.CancelledError:
        error = CANCELLED
    except Exception as failure:  # noqa: BLE001 - the outcome must be recorded, not raised
        error = f"upstream closed: {failure}"
    finally:
        try:
            await upstream.aclose()
        except Exception:  # noqa: BLE001, S110 - closing a dead connection must not fail the store
            pass
    sink.finish(error=error)


def begin(exec_id: str, role: str, path: str, status: int, content_type: str, upstream) -> Sink:
    """Start retaining one upstream response in its own task. Returns the live sink."""
    sink = Sink(exec_id, role, path, status, content_type)
    TASKS[exec_id] = asyncio.create_task(_pump(sink, upstream), name=f"omni-exec-{exec_id}")
    return sink


def record(exec_id: str, role: str, path: str, status: int, content_type: str,
           body: bytes, error: str | None = None) -> dict:
    """Remember one already-complete response (an error, or a direct call's answer) for replay."""
    sink = Sink(exec_id, role, path, status, content_type)
    sink.stream.extend(body[:MAX_BODY_BYTES])
    return sink.finish(error=error)


async def run(exec_id: str, role: str, path: str, call):
    """Run `call` (returning a Response) to completion, shielded from the requesting client.

    A disconnect cancels this wait but never the work: the answer is stored either way.
    Returns the stored entry; the caller replays it with `http_response`.
    """
    async def complete() -> None:
        try:
            response: Response = await call()
            record(exec_id, role, path, response.status_code,
                   response.headers.get("content-type", "application/octet-stream"),
                   response.body)
        except asyncio.CancelledError:
            cancelled(exec_id, role, path)
        except Exception as failure:  # noqa: BLE001 - the outcome must be recorded, not raised
            record(exec_id, role, path, 502, "application/json",
                   b'{"error": {"message": "upstream failed", "type": "upstream_error"}}',
                   error=str(failure))

    if exec_id not in ACTIVE:
        _show_running(exec_id, role, path)
    task = TASKS[exec_id] = asyncio.create_task(complete(), name=f"omni-exec-{exec_id}")
    await asyncio.shield(task)
    entry = DONE.get(exec_id)
    if entry is None:
        raise RuntimeError(f"execution {exec_id} vanished")
    return entry


def http_response(entry: dict):
    """Replay a stored execution exactly as it answered the first time."""
    return Response(content=entry["body"], status_code=entry["status_code"],
                    media_type=entry["content_type"])
