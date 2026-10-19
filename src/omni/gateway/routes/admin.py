"""The /admin surface: models (list, search, pull, delete, default, load, unload), workers and memory."""
import asyncio
import json

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from huggingface_hub.errors import GatedRepoError, HfHubHTTPError

from omni import config, registry
from omni.errors import error_response
from omni.gateway import executions, residency
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
        role, raw_model = payload.get("role"), payload.get("model")
        if role not in registry.ROLES:
            raise _bad_role()
        if not isinstance(raw_model, str) or not raw_model.strip():
            raise HTTPException(400, "model: must be a non-empty string")
        return role, raw_model.strip()

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
        model = registry.normalize_repo_id(model)
        if registry.runner(role) != "ollama":
            model = registry.bare_repo_id(model)
        # the download belongs to the server from here on: this stream only watches it, so
        # closing the tab, navigating away or closing the browser never cancels the download
        task = registry.ensure_started(role, model, clients["chat"])

        async def events():
            warned = 0
            while not task.done():
                sample = registry.progress(model) or {"status": f"downloading {model}"}
                for warning in sample.get("warnings", [])[warned:]:
                    yield json.dumps({"warning": warning}) + "\n"
                    warned += 1
                yield json.dumps({key: value for key, value in sample.items() if key != "warnings"}) + "\n"
                await asyncio.sleep(1.0)
            terminal = registry.outcome(model) or {"error": "download interrupted"}
            for warning in terminal.get("warnings", [])[warned:]:
                yield json.dumps({"warning": warning}) + "\n"
            terminal = {key: value for key, value in terminal.items()
                        if key not in ("finished_at", "warnings")}
            yield json.dumps(terminal) + "\n"

        return StreamingResponse(events(), media_type="application/x-ndjson")

    @app.post("/admin/models/delete")
    async def admin_delete(request: Request) -> JSONResponse:
        role, model = await model_request(request)
        model = registry.normalize_repo_id(model)
        if registry.runner(role) != "ollama":
            model = registry.bare_repo_id(model)
        users = [name for name in registry.ROLES if registry.matches(settings[name]["model"], model)]
        if users:
            return error_response(409, f"{model} is the default for {', '.join(users)}; switch that first", "conflict")
        try:
            if registry.runner(role) == "ollama":
                await registry.ollama_delete(clients["chat"], model)
            else:
                await asyncio.to_thread(registry.hf_delete, model)
        except httpx.HTTPError as error:
            return error_response(502, f"ollama: {error}", "upstream_error")
        except FileNotFoundError as error:
            return error_response(404, str(error), "not_found")
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
        match = next((entry for entry in available if registry.matches(entry["name"], model)), None)
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

    def setup_state(role: str, available: list[dict]) -> dict:
        """Whether the role's default can actually run: on disk, missing, or refused by its runtime."""
        default = settings[role]["model"]
        match = next((entry for entry in available if registry.matches(entry["name"], default)), None)
        total = sum(entry.get("size_bytes") or 0 for entry in available)
        if match is not None:
            return {"state": "ready", "reason": None, "total_bytes": total}
        if not registry.supports(role, default):
            marker = registry.family(role)
            hint = f" (it loads only repos with '{marker}' in the name)" if marker else ""
            return {"state": "incompatible",
                    "reason": f"{default}: the {role} runtime cannot run it{hint}; pick another default",
                    "total_bytes": total}
        if registry.runner(role) == "ollama":
            return {"state": "missing",
                    "reason": f"{default} is not installed; install it first",
                    "total_bytes": total}
        return {"state": "missing",
                "reason": f"{default} is not downloaded yet; loading downloads it first",
                "total_bytes": total}

    @app.get("/admin/models/status")
    async def admin_status() -> JSONResponse:
        """One setup summary per role: its default, what is on disk for it, and whether the default can run."""
        roles = {}
        for role in registry.ROLES:
            try:
                available = await installed_for(role)
            except (httpx.HTTPError, *HF_ERRORS):
                available = []
            state = setup_state(role, available)
            roles[role] = {"default": settings[role]["model"], "runner": registry.runner(role),
                           "installed": available, **state}
        return JSONResponse({"roles": roles})

    @app.get("/admin/models/downloads")
    async def admin_downloads() -> JSONResponse:
        """Every model download in flight, whatever started it, for progress bars."""
        return JSONResponse({"downloads": registry.snapshot(), "recent": registry.recent()})

    @app.get("/admin/requests")
    async def admin_requests() -> JSONResponse:
        """Retained inference executions: what is running now and what finished lately."""
        return JSONResponse({"running": executions.running(), "recent": executions.recent()})

    @app.get("/admin/requests/{exec_id}")
    async def admin_request(exec_id: str) -> JSONResponse:
        """One retained execution: live byte counts while it runs, the outcome when done."""
        entry = executions.get(exec_id)
        if entry is None:
            return error_response(404, f"no retained request {exec_id}", "not_found")
        return JSONResponse({key: value for key, value in entry.items() if key != "body"})

    @app.delete("/admin/requests/{exec_id}")
    async def admin_cancel_request(exec_id: str) -> JSONResponse:
        """Stop a running execution for good: the Stop button, not a closed tab."""
        if not executions.cancel(exec_id):
            return error_response(404, f"no running request {exec_id}", "not_found")
        return JSONResponse({"id": exec_id, "cancelled": True})

    @app.get("/admin/requests/{exec_id}/result")
    async def admin_request_result(exec_id: str) -> Response:
        """The retained response itself, exactly as it answered the first time."""
        entry = executions.get(exec_id)
        if entry is None:
            return error_response(404, f"no retained request {exec_id}", "not_found")
        if entry["status"] == "running":
            return error_response(409, f"request {exec_id} is still running", "conflict")
        return executions.http_response(entry)

    @app.get("/admin/models/check")
    async def admin_check(role: str, model: str = Query(..., min_length=1)) -> JSONResponse:
        """Can this role run this model id or page URL, before downloading anything?"""
        if role not in registry.ROLES:
            raise _bad_role()
        name = registry.normalize_repo_id(model.strip())
        if registry.runner(role) != "ollama":
            name = registry.bare_repo_id(name)
        if not registry.supports(role, name):
            marker = registry.family(role)
            hint = f" (it loads only repos with '{marker}' in the name)" if marker else ""
            return JSONResponse({"role": role, "model": name, "supported": False,
                                 "reason": f"the {role} runtime cannot run {name}{hint}",
                                 "installed": False, "size_bytes": None, "blockers": []})
        try:
            available = await installed_for(role)
        except (httpx.HTTPError, *HF_ERRORS):
            available = []
        match = next((entry for entry in available if registry.matches(entry["name"], name)), None)
        if match is not None:
            size = match.get("size_bytes") or 0
            return JSONResponse({"role": role, "model": name, "supported": True, "reason": None,
                                 "installed": True, "size_bytes": size,
                                 "blockers": registry.fit_blockers(size)})
        size = None
        gated = False
        if registry.runner(role) != "ollama":
            try:
                size = await asyncio.to_thread(registry.hf_size, name)
            except GatedRepoError:
                gated = True
            except HF_ERRORS:
                size = None
        return JSONResponse({"role": role, "model": name, "supported": True, "reason": None,
                             "installed": False, "size_bytes": size, "gated": gated,
                             "accept_url": f"https://huggingface.co/{name}" if gated else None,
                             "blockers": registry.fit_blockers(size) if size else []})

    async def residency_request(request: Request) -> tuple[str, str, bool]:
        """The role, model and force flag a load/unload names; the model defaults to the role's default."""
        payload = await json_object(request)
        role = payload.get("role")
        if role not in registry.ROLES:
            raise _bad_role()
        raw_model = payload.get("model")
        if raw_model is not None and not isinstance(raw_model, str):
            raise HTTPException(400, "model: must be a string")
        model = (raw_model or settings[role]["model"]).strip()
        if not model:
            raise HTTPException(400, "model: must be a non-empty string")
        if role in registry.PYTHON_WORKERS and model != settings[role]["model"]:
            raise HTTPException(400, f"the {role} worker serves only its default, {settings[role]['model']}; "
                                     f"make {model} the default first")
        return role, model, bool(payload.get("force"))

    async def wake(role: str) -> None:
        """Asking for a model means asking for its worker: a stopped (held) one would never load it."""
        worker = pool.workers.get(role)
        if worker is not None and worker.held:
            await pool.restart(role)

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
        if load:
            await wake(role)
        try:
            loaded = await residency.set_residency(ctx, role, model, load)
        except httpx.TransportError:
            pool.note_unreachable(role)
            return error_response(503, ctx.proxy.unavailable(role), "unavailable")
        except httpx.HTTPStatusError as error:
            return error_response(502, f"{role}: {error.response.text}", "upstream_error")
        except OSError as error:
            return error_response(502, f"{role}: {error}", "upstream_error")
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
        raw_model = payload.get("model")
        if raw_model is not None and not isinstance(raw_model, str):
            raise HTTPException(400, "model: must be a string")
        model = (raw_model or settings[role]["model"]).strip()
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
            await wake(role)
        try:
            loaded = await residency.set_residency(ctx, role, model, pinned, keep_warm=pinned)
        except httpx.TransportError:
            # the worker is not up yet: the pin stands, and the keep-warm loop loads it once it is
            pool.note_unreachable(role)
            loaded = False
        except httpx.HTTPStatusError as error:
            if pinned:
                return error_response(502, f"{role}: {error.response.text}", "upstream_error")
            loaded = False  # unpin must succeed even when the model is already gone
        except OSError as error:
            # the model could not be downloaded before loading it
            return error_response(502, f"{role}: {error}", "upstream_error")
        current = config.load_pins()
        stored = config.save_pins([*current, role] if pinned else [name for name in current if name != role])
        return JSONResponse({"role": role, "model": model, "pinned": role in stored,
                             "loaded": pinned if loaded is None else bool(loaded), "pins": stored,
                             "advisories": advisories, "blockers": blockers})

    @app.post("/admin/workers/{name}/{action}")
    async def admin_worker(name: str, action: str) -> JSONResponse:
        if name == "all":
            if action not in ("restart", "stop"):
                return error_response(400, "action: restart or stop", "invalid_request_error")
            for worker in pool.workers:
                if action == "stop":
                    await pool.hold(worker)
                else:
                    await pool.restart(worker)
            return JSONResponse({"worker": "all", "status": {name: pool.workers[name].status for name in pool.workers}})
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
