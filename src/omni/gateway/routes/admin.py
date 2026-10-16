"""The /admin surface: models (list, search, pull, delete, default, load, unload), workers and memory."""
import asyncio
import json

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from huggingface_hub.errors import HfHubHTTPError

from omni import config, registry
from omni.errors import error_response
from omni.gateway import residency
from omni.gateway.context import Context
from omni.gateway.http import json_object

HF_ERRORS = (HfHubHTTPError, OSError)


def _bad_role() -> HTTPException:
    return HTTPException(400, f"role: one of {', '.join(registry.ROLES)}")


def register(app: FastAPI, ctx: Context) -> None:
    settings, clients, pool = ctx.settings, ctx.clients, ctx.pool

    async def installed_for(role: str) -> list[dict]:
        return await registry.installed_for(role, clients["chat"])

    async def model_request(request: Request) -> tuple[str, str]:
        """The role and model a /admin/models request names."""
        payload = await json_object(request)
        role, model = payload.get("role"), str(payload.get("model", "")).strip()
        if role not in registry.ROLES:
            raise _bad_role()
        if not model:
            raise HTTPException(400, "model: required")
        return role, model

    @app.get("/admin/models")
    async def admin_models() -> JSONResponse:
        try:
            ollama = await registry.ollama_installed(clients["chat"])
        except (httpx.HTTPError, ValueError):
            ollama = []
        try:
            huggingface = await asyncio.to_thread(registry.hf_installed)
        except OSError:
            huggingface = []
        installed_by_role = {}
        for role in registry.ROLES:
            try:
                installed_by_role[role] = [model["name"] for model in await installed_for(role)]
            except (httpx.HTTPError, *HF_ERRORS):
                installed_by_role[role] = []
        roles = {role: {"default": settings[role]["model"], "runner": registry.runner(role)}
                 for role in registry.ROLES}
        return JSONResponse({"roles": roles, "installed": {"ollama": ollama, "huggingface": huggingface},
                             "installed_by_role": installed_by_role})

    @app.post("/admin/models/pull")
    async def admin_pull(request: Request) -> StreamingResponse:
        role, model = await model_request(request)

        async def events():
            async for event in registry.pull(clients["chat"], role, model):
                yield json.dumps(event) + "\n"

        return StreamingResponse(events(), media_type="application/x-ndjson")

    @app.post("/admin/models/delete")
    async def admin_delete(request: Request) -> JSONResponse:
        role, model = await model_request(request)
        users = [name for name in registry.ROLES if settings[name]["model"] == model]
        if users:
            return error_response(409, f"{model} is the default for {', '.join(users)}; switch that first", "conflict")
        try:
            if registry.runner(role) == "ollama":
                await registry.ollama_delete(clients["chat"], model)
            else:
                await asyncio.to_thread(registry.hf_delete, model)
        except httpx.HTTPError as error:
            return error_response(502, f"ollama: {error}", "upstream_error")
        except HF_ERRORS as error:
            return error_response(502, f"huggingface: {error}", "upstream_error")
        return JSONResponse({"deleted": model})

    @app.post("/admin/models/default")
    async def admin_default(request: Request) -> JSONResponse:
        role, model = await model_request(request)
        try:
            available = await installed_for(role)
        except httpx.HTTPError as error:
            return error_response(503, f"ollama: {error}", "unavailable")
        except HF_ERRORS as error:
            return error_response(503, f"huggingface: {error}", "unavailable")
        match = next((entry for entry in available if entry["name"] in (model, f"{model}:latest")), None)
        if match is None:
            return error_response(400, f"{model} is not installed for {role}, or the {role} runtime cannot run it")
        config.save_override(role, "model", model)
        settings[role]["model"] = model
        if role in registry.PYTHON_WORKERS:
            await pool.restart(role)
        return JSONResponse({"role": role, "model": model, "warnings": registry.fit_blockers(match["size_bytes"])})

    @app.get("/admin/models/search")
    async def admin_search(role: str, q: str = "", limit: int = Query(20, ge=1, le=50)) -> JSONResponse:
        if role not in registry.ROLES:
            raise _bad_role()
        try:
            results = await asyncio.to_thread(registry.search, role, q, limit)
        except (httpx.HTTPError, *HF_ERRORS) as error:
            return error_response(502, f"huggingface: {error}", "upstream_error")
        return JSONResponse({"role": role, "results": results})

    async def residency_request(request: Request) -> tuple[str, str, bool]:
        """The role, model and force flag a load/unload names; the model defaults to the role's default."""
        payload = await json_object(request)
        role = payload.get("role")
        if role not in registry.ROLES:
            raise _bad_role()
        model = str(payload.get("model") or settings[role]["model"]).strip()
        if role in registry.PYTHON_WORKERS and model != settings[role]["model"]:
            raise HTTPException(400, f"the {role} worker serves only its default, {settings[role]['model']}; "
                                     f"make {model} the default first")
        return role, model, bool(payload.get("force"))

    async def residence(role: str, model: str, load: bool, force: bool) -> JSONResponse:
        """Load or unload, refusing without force only for something about this model.

        Machine-wide pressure (swap, CPU, load) is reported as advice, never as a gate: it is the same
        whatever you load, so gating on it would put a confirmation in front of every single load.
        """
        plan = await residency.plan(ctx, role, model) if load else residency.Plan([], [], None)
        if plan.fatal:
            return error_response(409, plan.fatal, "conflict")
        if plan.blockers and not force:
            return error_response(409, "; ".join(plan.blockers) + ". Send force: true to load anyway.",
                                  "overload", blockers=plan.blockers)
        try:
            loaded = await residency.set_residency(ctx, role, model, load)
        except httpx.TransportError:
            pool.note_unreachable(role)
            return error_response(503, ctx.proxy.unavailable(role), "unavailable")
        except httpx.HTTPStatusError as error:
            return error_response(502, f"{role}: {error.response.text}", "upstream_error")
        # report what the runtime actually holds, falling back to the request when it does not say
        return JSONResponse({"role": role, "model": model, "loaded": load if loaded is None else loaded,
                             "advisories": plan.advisories, "blockers": plan.blockers})

    async def residency_route(request: Request, load: bool) -> JSONResponse:
        role, model, force = await residency_request(request)
        return await residence(role, model, load, force)

    @app.post("/admin/models/load")
    async def admin_load(request: Request) -> JSONResponse:
        return await residency_route(request, load=True)

    @app.post("/admin/models/unload")
    async def admin_unload(request: Request) -> JSONResponse:
        return await residency_route(request, load=False)

    @app.get("/admin/pins")
    async def admin_pins() -> dict:
        return {"pinned": config.load_pins()}

    @app.post("/admin/pins")
    async def admin_set_pin(request: Request) -> JSONResponse:
        """Keep a role's model resident (pinned), or stop keeping it and unload it.

        Pinning loads the model now and holds it: a worker is re-loaded before it would idle out, and
        an Ollama model is held with keep_alive = -1. The same refusal as a plain load applies. A pin
        is a standing instruction, so it is recorded even when the worker is not up to act on it yet.
        """
        payload = await json_object(request)
        role = payload.get("role")
        if role not in registry.ROLES:
            raise _bad_role()
        pinned = bool(payload.get("pinned"))
        model = str(payload.get("model") or settings[role]["model"]).strip()
        if role in registry.PYTHON_WORKERS and model != settings[role]["model"]:
            raise HTTPException(400, f"the {role} worker serves only its default, {settings[role]['model']}")
        force = bool(payload.get("force"))
        advisories, blockers = [], []
        if pinned:
            plan = await residency.plan(ctx, role, model)
            advisories, blockers = plan.advisories, plan.blockers
            if plan.fatal:
                return error_response(409, plan.fatal, "conflict")
            if blockers and not force:
                return error_response(409, "; ".join(blockers) + ". Send force: true to load anyway.",
                                      "overload", blockers=blockers)
        try:
            loaded = await residency.set_residency(ctx, role, model, pinned, keep_warm=pinned)
        except httpx.TransportError:
            # the worker is not up yet: the pin stands, and the keep-warm loop loads it once it is
            pool.note_unreachable(role)
            loaded = False
        except httpx.HTTPStatusError as error:
            return error_response(502, f"{role}: {error.response.text}", "upstream_error")
        current = config.load_pins()
        stored = config.save_pins([*current, role] if pinned else [name for name in current if name != role])
        return JSONResponse({"role": role, "model": model, "pinned": role in stored,
                             "loaded": pinned if loaded is None else bool(loaded), "pins": stored,
                             "advisories": advisories, "blockers": blockers})

    @app.post("/admin/workers/{name}/{action}")
    async def admin_worker(name: str, action: str) -> JSONResponse:
        if name not in pool.workers:
            if name == "chat":
                return error_response(409, f"ollama runs outside omni at {ctx.chat_url}; manage it there", "conflict")
            return error_response(404, f"no worker named {name}", "not_found")
        if action not in ("start", "stop", "restart"):
            return error_response(400, "action: start, stop or restart", "invalid_request_error")
        if action == "stop":
            await pool.hold(name)
        else:
            await pool.restart(name)
        return JSONResponse({"worker": name, "status": pool.workers[name].status})

    @app.get("/admin/memory")
    async def admin_memory() -> dict:
        return await registry.memory(clients["chat"], pool.status())
