"""The worker process: a FastAPI app around one lazily-loaded model, with /load and /unload.

Errors leave in the same OpenAI shape the gateway uses.
"""
import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

from fastapi import FastAPI

from omni.errors import install_error_handlers
from omni.workers.runtime import Lazy

__all__ = ["Lazy", "app"]

Lifespan = Callable[[FastAPI], AbstractAsyncContextManager[Any]]


def app(model: Lazy, lifespan: Lifespan | None = None) -> FastAPI:
    """A worker app whose failures use the OpenAI error shape, with routes to load or drop its model."""
    api = FastAPI(lifespan=lifespan)
    install_error_handlers(api, expose_internal=True)

    @api.post("/load")
    async def load() -> dict:
        await asyncio.to_thread(model.load)
        return {"loaded": model.loaded}

    @api.post("/unload")
    async def unload() -> dict:
        await asyncio.to_thread(model.unload)
        return {"loaded": model.loaded}

    return api
