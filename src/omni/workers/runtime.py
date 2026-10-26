"""Load a model on first use; drop it when the gateway says so.

One lock covers load, use and unload, so calls into the model never overlap (Metal aborts when two
threads encode at once). Idle unloading is the gateway's job (`gateway/keepwarm.py`): it sees every
request and every pin, so a worker never drops a model on its own clock.
"""
import asyncio
import gc
import threading
from collections.abc import Callable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from typing import Any

from fastapi import HTTPException


class ModelExecutor:
    """Run model work on one dedicated thread.

    MLX streams are thread-local: a model loaded on one thread raises
    ``There is no Stream(...) in current thread`` when used from another.
    ``asyncio.to_thread`` picks any pool thread, so load and inference must
    both go through this single-thread executor to stay on the thread that
    loaded the model.
    """

    def __init__(self, name: str = "model", max_pending: int = 0) -> None:
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=name)
        # 0 means unlimited; otherwise requests past this many running + waiting are refused
        self.max_pending = max_pending
        self.pending = 0

    def submit(self, fn: Callable[..., Any], *args: Any) -> Future:
        return self._executor.submit(fn, *args)

    async def run(self, fn: Callable[..., Any], *args: Any) -> Any:
        loop = asyncio.get_running_loop()
        self.pending += 1
        try:
            return await loop.run_in_executor(self._executor, fn, *args)
        finally:
            self.pending -= 1

    async def run_request(self, fn: Callable[..., Any], *args: Any) -> Any:
        """`run` for an inference request: refused with a 429 instead of queueing past the gateway's
        read timeout, where the client would give up while the model still did the work."""
        if self.max_pending and self.pending >= self.max_pending:
            raise HTTPException(429, f"{self.pending} requests are already running or waiting; retry later")
        return await self.run(fn, *args)


class Lazy:
    def __init__(self, load: Callable[[], Any], after_unload: Callable[[], None] | None = None):
        self._load = load
        self._after_unload = after_unload
        self._model: Any = None
        self._lock = threading.Lock()

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        with self.use():
            pass

    def unload(self) -> None:
        with self._lock:
            if self._model is None:
                return
            self._model = None
            gc.collect()
            if self._after_unload:
                try:
                    self._after_unload()
                except Exception:  # noqa: BLE001, S110 - a failed cache clear must not fail the unload
                    pass

    @contextmanager
    def use(self) -> Iterator[Any]:
        with self._lock:
            if self._model is None:
                self._model = self._load()
            yield self._model
