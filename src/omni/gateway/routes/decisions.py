"""POST /v1/systemone, answered by the decide worker."""
from fastapi import FastAPI, Request
from fastapi.responses import Response

from omni.gateway import residency
from omni.gateway.context import Context

# a decoded image can be 25 MB, so a base64 one needs a body a bit over 33 MB
SYSTEMONE_MAX_BYTES = 36 * 1024 * 1024


def register(app: FastAPI, ctx: Context) -> None:
    @app.post("/v1/systemone")
    async def systemone(request: Request) -> Response:
        return await ctx.proxy.forward(request, "decide", "/v1/systemone", max_bytes=SYSTEMONE_MAX_BYTES,
                                       ready=lambda: residency.ensure_ready(ctx, "decide"))
