"""POST /v1/systemone, answered by the decide worker."""
from fastapi import FastAPI, Request
from fastapi.responses import Response

from omni.gateway import residency
from omni.gateway.context import Context


def register(app: FastAPI, ctx: Context) -> None:
    @app.post("/v1/systemone")
    async def systemone(request: Request) -> Response:
        return await ctx.proxy.forward(request, "decide", "/v1/systemone",
                                       ready=lambda: residency.ensure_ready(ctx, "decide"))
