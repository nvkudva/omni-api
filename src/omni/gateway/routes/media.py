"""POST /v1/images/generations and the /v1/videos job routes."""
from fastapi import FastAPI, Request
from fastapi.responses import Response

from omni.errors import error_response
from omni.gateway import residency
from omni.gateway.context import Context

IMAGE_MAX_BYTES = 8 * 1024 * 1024
# a base64 first frame can be ~25 MB decoded, so the JSON body needs more headroom
VIDEO_MAX_BYTES = 48 * 1024 * 1024


def register(app: FastAPI, ctx: Context) -> None:
    @app.post("/v1/images/generations")
    async def images(request: Request) -> Response:
        refused = await residency.ensure_ready(ctx, "image")
        if refused is not None:
            return error_response(502, refused, "upstream_error")
        return await ctx.proxy.forward(request, "image", "/v1/images/generations", max_bytes=IMAGE_MAX_BYTES)

    @app.post("/v1/videos")
    async def create_video(request: Request) -> Response:
        refused = await residency.ensure_ready(ctx, "video")
        if refused is not None:
            return error_response(502, refused, "upstream_error")
        return await ctx.proxy.forward(request, "video", "/v1/videos", max_bytes=VIDEO_MAX_BYTES)

    @app.get("/v1/videos/{video_id}")
    async def video_status(request: Request, video_id: str) -> Response:
        return await ctx.proxy.forward(request, "video", f"/v1/videos/{video_id}")

    @app.get("/v1/videos/{video_id}/content")
    async def video_content(request: Request, video_id: str) -> Response:
        return await ctx.proxy.forward(request, "video", f"/v1/videos/{video_id}/content")
