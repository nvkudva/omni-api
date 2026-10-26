"""The /admin runtime surface: retained requests, load/unload, pins, workers and memory."""
import asyncio

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from omni import config, registry
from omni.errors import error_response
from omni.gateway import executions, residency
from omni.gateway.context import Context
from omni.gateway.http import bad_role, json_object


def register(app: FastAPI, ctx: Context) -> None:
    settings, clients, pool = ctx.settings, ctx.clients, ctx.pool

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
        return executions.result_response(entry)

    async def role_and_model(request: Request) -> tuple[dict, str, str]:
        """The payload, role and model a load/unload/pin names; the model defaults to the role's default."""
        payload = await json_object(request)
        role = payload.get("role")
        if role not in registry.ROLES:
            raise bad_role()
        raw_model = payload.get("model")
        if raw_model is not None and not isinstance(raw_model, str):
            raise HTTPException(400, "model: must be a string")
        model = (raw_model or settings[role]["model"]).strip()
        if not model:
            raise HTTPException(400, "model: must be a non-empty string")
        if role in registry.PYTHON_WORKERS and model != settings[role]["model"]:
            raise HTTPException(400, f"the {role} worker serves only its default, {settings[role]['model']}; "
                                     f"make {model} the default first")
        return payload, role, model

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
        except httpx.TimeoutException:
            return error_response(504, f"{role} did not finish loading in time; it may still be loading", "timeout")
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
        payload, role, model = await role_and_model(request)
        return await residence(role, model, load, bool(payload.get("force")))

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
        payload, role, model = await role_and_model(request)
        pinned = bool(payload.get("pinned"))
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
        except httpx.TimeoutException:
            # still loading: the pin stands, and the keep-warm loop picks it up
            loaded = False
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
            reason = registry.unavailable_reason(name)
            if reason:
                return error_response(409, reason, "conflict")
            return error_response(404, f"no worker named {name}", "not_found")
        if action not in ("start", "stop", "restart"):
            return error_response(400, "action: start, stop or restart", "invalid_request_error")
        worker = pool.workers[name]
        if action == "stop":
            await pool.hold(name)
        elif action == "restart" or worker.held or worker.status in ("stopped", "failed"):
            await pool.restart(name)
        return JSONResponse({"worker": name, "status": pool.workers[name].status})

    @app.get("/admin/memory")
    async def admin_memory() -> dict:
        return await registry.memory(clients["chat"], await asyncio.to_thread(pool.status))
