"""POST /v1/systemone and POST /v1/embeddings: gateway logic answered by Ollama."""
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from omni import registry
from omni.errors import UpstreamError, error_response
from omni.gateway.context import Context
from omni.gateway.http import json_object, optional_model
from omni.inference import decide, embed


def register(app: FastAPI, ctx: Context) -> None:
    async def on_ollama(request: Request, role: str, run) -> JSONResponse:
        """Parse the JSON body and answer it with `run(ollama_client, model, payload)`."""
        payload = await json_object(request)
        model = optional_model(payload)
        if not model or model == role:
            model = ctx.settings[role]["model"]
        embed_model = ctx.settings["embed"]["model"]
        if role == "embed" and not registry.matches(model, embed_model) and any(
                registry.matches(model, ctx.settings[other]["model"]) for other in ("chat", "decide")):
            return error_response(400, f"{model} does not support embeddings; it is not the embed model", "invalid_request_error")
        if role != "embed" and registry.matches(model, embed_model) and not registry.matches(model, ctx.settings[role]["model"]):
            return error_response(400, f"{model} is the embed model; it does not support {role}", "invalid_request_error")
        try:
            return JSONResponse(await run(ctx.clients["chat"], model, payload))
        except ValueError as error:
            return error_response(400, str(error), "invalid_request_error")
        except UpstreamError as error:
            return error_response(502, str(error), "upstream_error")
        except httpx.TransportError:
            return error_response(
                503, f"ollama is not ready ({ctx.proxy.state('chat')}); GET /health shows progress", "unavailable")
        except httpx.HTTPStatusError as error:
            return error_response(error.response.status_code, f"ollama: {error.response.text}", "upstream_error")

    @app.post("/v1/systemone")
    async def systemone(request: Request) -> JSONResponse:
        return await on_ollama(request, "decide", decide.run)

    @app.post("/v1/embeddings")
    async def embeddings(request: Request) -> JSONResponse:
        return await on_ollama(request, "embed", embed.run)
