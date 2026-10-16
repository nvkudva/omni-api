import asyncio
import gc
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse


def _error(status: int, message: str, kind: str) -> JSONResponse:
    return JSONResponse({"error": {"message": message, "type": kind}}, status_code=status)


def app(model: "Lazy") -> FastAPI:
    """A worker app whose failures use the OpenAI error shape, like the gateway's own,
    with routes to load or drop its model on demand."""
    api = FastAPI()

    @api.post("/load")
    async def load() -> dict:
        await asyncio.to_thread(model.load)
        return {"loaded": model.loaded}

    @api.post("/unload")
    async def unload() -> dict:
        await asyncio.to_thread(model.unload)
        return {"loaded": model.loaded}

    @api.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException) -> JSONResponse:
        return _error(error.status_code, str(error.detail), "invalid_request_error")

    @api.exception_handler(Exception)
    async def server_error(request: Request, error: Exception) -> JSONResponse:
        return _error(500, f"{type(error).__name__}: {error}", "server_error")

    return api


class Lazy:
    """A model loaded on first use and dropped after `idle_seconds` unused.
    One lock covers load, use and unload, so calls into the model never overlap
    (Metal aborts when two threads encode at once)."""

    def __init__(self, load: Callable[[], Any], idle_seconds: float, after_unload: Callable[[], None] | None = None):
        self._load = load
        self._idle_seconds = idle_seconds
        self._after_unload = after_unload
        self._model: Any = None
        self._last_used = 0.0
        self._lock = threading.Lock()
        threading.Thread(target=self._unload_when_idle, daemon=True).start()

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        with self.use():
            pass

    def unload(self) -> None:
        with self._lock:
            self._drop()

    def _drop(self) -> None:
        if self._model is None:
            return
        self._model = None
        gc.collect()
        if self._after_unload:
            self._after_unload()

    @contextmanager
    def use(self) -> Iterator[Any]:
        with self._lock:
            if self._model is None:
                self._model = self._load()
            try:
                yield self._model
            finally:
                self._last_used = time.monotonic()

    def _unload_when_idle(self) -> None:
        while True:
            time.sleep(30)
            with self._lock:
                idle_for = time.monotonic() - self._last_used
                if idle_for >= self._idle_seconds:
                    self._drop()
