"""GET /v1/models and GET /health."""
import asyncio

import httpx
from fastapi import FastAPI

from omni import registry
from omni.gateway.context import Context

READY_STATES = ("ready", "external")


async def _chat_models(client: httpx.AsyncClient) -> list[str]:
    try:
        response = await client.get("/api/tags", timeout=2.0)
        return [model["name"] for model in response.json().get("models", [])]
    except (httpx.HTTPError, ValueError):
        return []


def register(app: FastAPI, ctx: Context) -> None:
    @app.get("/v1/models")
    async def list_models() -> dict:
        names = await _chat_models(ctx.clients["chat"])
        found = await asyncio.gather(*(registry.capabilities(ctx.clients["chat"], name) for name in names))
        data = [{"id": name, "object": "model", "owned_by": "omni", "role": "chat", "capabilities": capabilities}
                for name, capabilities in zip(names, found)]
        for role in ("decide", "embed") + registry.PYTHON_WORKERS:
            data.append({"id": ctx.settings[role]["model"], "object": "model", "owned_by": "omni", "role": role,
                         "capabilities": []})
        defaults = {role: ctx.settings[role]["model"] for role in registry.ROLES}
        return {"object": "list", "data": data, "defaults": defaults}

    @app.get("/health")
    async def health() -> dict:
        statuses = ctx.pool.status()
        if "chat" not in statuses:
            statuses["chat"] = {"status": "external", "url": ctx.chat_url}
        # a worker the admin stopped on purpose is not a reason for the gateway to report itself unhealthy
        ok = all(entry["status"] in READY_STATES or entry.get("held") for entry in statuses.values())
        return {"ok": ok, "workers": statuses}
