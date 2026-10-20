"""GET /v1/models and GET /health."""
import asyncio

import httpx
from fastapi import FastAPI

from omni import config, registry
from omni.gateway.context import Context

READY_STATES = ("ready", "external")


async def _chat_models(client: httpx.AsyncClient) -> list[str]:
    try:
        response = await client.get("/api/tags", timeout=2.0)
        return [model["name"] for model in response.json().get("models", [])]
    except (httpx.HTTPError, ValueError):
        return []


def _role_for(name: str, capabilities: list[str], settings: dict) -> str:
    for role in ("embed", "decide"):
        if registry.matches(name, settings[role]["model"]):
            return role
    if capabilities and "completion" not in capabilities:
        return "embed" if "embedding" in capabilities else "chat"
    return "chat"


def register(app: FastAPI, ctx: Context) -> None:
    @app.get("/v1/models")
    async def list_models() -> dict:
        names = await _chat_models(ctx.clients["chat"])
        found = await asyncio.gather(*(registry.capabilities(ctx.clients["chat"], name) for name in names))
        data = [{"id": name, "object": "model", "owned_by": "omni",
                 "role": _role_for(name, capabilities, ctx.settings), "capabilities": capabilities}
                for name, capabilities in zip(names, found)]
        seen = {registry.canonical(entry["id"]) for entry in data}
        for role in ("decide", "embed") + registry.PYTHON_WORKERS:
            model = ctx.settings[role]["model"]
            if registry.canonical(model) in seen:
                continue
            seen.add(registry.canonical(model))
            data.append({"id": model, "object": "model", "owned_by": "omni", "role": role,
                         "capabilities": []})
        defaults = {role: ctx.settings[role]["model"] for role in registry.ROLES}
        return {"object": "list", "data": data, "defaults": defaults}

    @app.get("/health")
    async def health() -> dict:
        statuses = await asyncio.to_thread(ctx.pool.status)
        if "chat" not in statuses:
            reachable = bool(await _chat_models(ctx.clients["chat"]))
            statuses["chat"] = {"status": "external" if reachable else "unavailable", "url": ctx.chat_url}
        # a worker the admin stopped on purpose is not a reason for the gateway to report itself unhealthy
        ok = all(entry["status"] in READY_STATES or entry.get("held") for entry in statuses.values())
        # the pins a person set, so the sidebar can show which cards are meant to stay warm
        return {"ok": ok, "workers": statuses, "pins": config.load_pins()}
