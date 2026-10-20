"""POST /v1/audio/transcriptions, POST /v1/audio/speech and GET /v1/audio/voices."""
from fastapi import FastAPI, Request
from fastapi.responses import Response

from omni.gateway import residency
from omni.gateway.context import Context
from omni.schemas import STT_MAX_BYTES

# the multipart envelope adds a little on top of the audio itself
TRANSCRIPTION_MAX_BYTES = STT_MAX_BYTES + 8 * 1024 * 1024


def register(app: FastAPI, ctx: Context) -> None:
    @app.post("/v1/audio/transcriptions")
    async def transcriptions(request: Request) -> Response:
        return await ctx.proxy.forward(request, "stt", "/v1/audio/transcriptions",
                                       max_bytes=TRANSCRIPTION_MAX_BYTES,
                                       ready=lambda: residency.ensure_ready(ctx, "stt"))

    @app.post("/v1/audio/speech")
    async def speech(request: Request) -> Response:
        return await ctx.proxy.forward(request, "tts", "/v1/audio/speech",
                                       ready=lambda: residency.ensure_ready(ctx, "tts"))

    @app.get("/v1/audio/voices")
    async def voices(request: Request) -> Response:
        return await ctx.proxy.forward(request, "tts", "/v1/audio/voices")
