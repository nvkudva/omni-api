"""The worker process: a FastAPI app around one lazily-loaded model, with /load and /unload.

Errors leave in the same OpenAI shape the gateway uses.
"""
import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

from fastapi import FastAPI

from omni.errors import install_error_handlers
from omni.workers.runtime import Lazy, ModelExecutor

__all__ = ["Lazy", "ModelExecutor", "app"]

Lifespan = Callable[[FastAPI], AbstractAsyncContextManager[Any]]


def app(model: Lazy, lifespan: Lifespan | None = None,
         model_executor: ModelExecutor | None = None) -> FastAPI:
    """A worker app whose failures use the OpenAI error shape, with routes to load or drop its model."""
    api = FastAPI(lifespan=lifespan)
    install_error_handlers(api, expose_internal=False)

    async def run_model(fn: Callable[[], Any]) -> Any:
        if model_executor is not None:
            return await model_executor.run(fn)
        return await asyncio.to_thread(fn)

    @api.post("/load")
    async def load() -> dict:
        await run_model(model.load)
        return {"loaded": model.loaded}

    @api.post("/unload")
    async def unload() -> dict:
        await run_model(model.unload)
        return {"loaded": model.loaded}

    return api
