"""Load a model on first use and drop it after it goes idle.

One lock covers load, use and unload, so calls into the model never overlap (Metal aborts when two
threads encode at once).
"""
import asyncio
import gc
import threading
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import Any

IDLE_CHECK_SECONDS = 30.0


class ModelExecutor:
    """Run model work on one dedicated thread.

    MLX streams are thread-local: a model loaded on one thread raises
    ``There is no Stream(...) in current thread`` when used from another.
    ``asyncio.to_thread`` picks any pool thread, so load and inference must
    both go through this single-thread executor to stay on the thread that
    loaded the model.
    """

    def __init__(self, name: str = "model") -> None:
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=name)

    async def run(self, fn: Callable[..., Any], *args: Any) -> Any:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, fn, *args)


class Lazy:
    def __init__(self, load: Callable[[], Any], idle_seconds: float,
                 after_unload: Callable[[], None] | None = None):
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
            try:
                self._after_unload()
            except Exception:  # noqa: BLE001, S110 - a failed cache clear must not kill the idle thread
                pass

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
            time.sleep(IDLE_CHECK_SECONDS)
            with self._lock:
                if self._model is not None and time.monotonic() - self._last_used >= self._idle_seconds:
                    self._drop()
