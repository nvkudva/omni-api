"""POST /v1/chat/completions, streamed straight through to Ollama."""
import json

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response

from omni import registry
from omni.gateway.context import Context
from omni.gateway.http import json_object
from omni.gateway.proxy import DEFAULT_MAX_BYTES


def register(app: FastAPI, ctx: Context) -> None:
    @app.post("/v1/chat/completions")
    async def chat(request: Request) -> Response:
        payload = await json_object(request)
        payload.setdefault("model", ctx.settings["chat"]["model"])
        embed_model = ctx.settings["embed"]["model"]
        if not registry.matches(payload["model"], ctx.settings["chat"]["model"]) and registry.matches(payload["model"], embed_model):
            raise HTTPException(400, f"{payload['model']} does not support chat; it is the embed model")
        body = json.dumps(payload).encode()
        if len(body) > DEFAULT_MAX_BYTES:
            raise HTTPException(413, "body: larger than 8 MB")
        return await ctx.proxy.forward(request, "chat", "/v1/chat/completions", body=body)
