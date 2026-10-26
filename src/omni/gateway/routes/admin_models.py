"""The /admin model catalog: list, search, check, pull, delete, set a default, and setup status."""
import asyncio
import json

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from huggingface_hub.errors import GatedRepoError

from omni import config, registry
from omni.errors import error_response
from omni.gateway.context import Context
from omni.gateway.http import bad_role, json_object
from omni.registry import pulling


def _unsupported(role: str, name: str) -> str | None:
    """Why this role's runtime cannot run `name`, or None when it can."""
    if registry.supports(role, name):
        return None
    markers = registry.family(role)
    hint = f" (it loads only repos with '{' or '.join(markers)}' in the name)" if markers else ""
    return f"the {role} runtime cannot run {name}{hint}"


def register(app: FastAPI, ctx: Context) -> None:
    settings, clients, pool = ctx.settings, ctx.clients, ctx.pool

    async def installed_for(role: str) -> list[dict]:
        return await registry.installed_for(role, clients["chat"])

    async def model_request(request: Request) -> tuple[str, str]:
        """The role and model a /admin/models request names."""
        payload = await json_object(request)
        role, raw_model = payload.get("role"), payload.get("model")
        if role not in registry.ROLES:
            raise bad_role()
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
            except (httpx.HTTPError, *registry.HF_ERRORS):
                installed_by_role[role] = []
        roles = {role: {"default": settings[role]["model"], "runner": registry.runner(role)}
                 for role in registry.ROLES}
        return JSONResponse({"roles": roles, "installed": {"ollama": ollama, "huggingface": huggingface},
                             "installed_by_role": installed_by_role})

    @app.post("/admin/models/pull")
    async def admin_pull(request: Request) -> Response:
        role, model = await model_request(request)
        model = registry.repo_name(role, model)
        reason = _unsupported(role, model)
        if reason:
            return error_response(400, reason, "invalid_request_error")
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

    @app.post("/admin/models/pull/stop")
    async def admin_pull_stop(request: Request) -> JSONResponse:
        role, model = await model_request(request)
        model = registry.repo_name(role, model)
        if not pulling.stop(model):
            return error_response(404, f"{model} is not downloading", "not_found")
        return JSONResponse({"stopped": model})

    @app.post("/admin/models/delete")
    async def admin_delete(request: Request) -> JSONResponse:
        role, model = await model_request(request)
        model = registry.repo_name(role, model)
        users = [name for name in registry.ROLES if registry.matches(settings[name]["model"], model)]
        if users:
            return error_response(409, f"{model} is the default for {', '.join(users)}; switch that first", "conflict")
        if registry.progress(model) or model in pulling.TASKS:
            return error_response(409, f"{model} is still downloading; wait for it to finish", "conflict")
        try:
            if registry.runner(role) == "ollama":
                await registry.ollama_delete(clients["chat"], model)
            else:
                await asyncio.to_thread(registry.hf_delete, model)
        except httpx.HTTPError as error:
            return error_response(502, f"ollama: {error}", "upstream_error")
        except FileNotFoundError as error:
            return error_response(404, str(error), "not_found")
        except registry.HF_ERRORS as error:
            return error_response(502, f"huggingface: {error}", "upstream_error")
        return JSONResponse({"deleted": model})

    @app.post("/admin/models/default")
    async def admin_default(request: Request) -> JSONResponse:
        role, model = await model_request(request)
        try:
            available = await installed_for(role)
        except httpx.HTTPError as error:
            return error_response(503, f"ollama: {error}", "unavailable")
        except registry.HF_ERRORS as error:
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
            raise bad_role()
        try:
            results = await asyncio.to_thread(registry.search, role, q, limit)
        except (httpx.HTTPError, *registry.HF_ERRORS) as error:
            return error_response(502, f"huggingface: {error}", "upstream_error")
        return JSONResponse({"role": role, "results": results})

    def setup_state(role: str, available: list[dict]) -> dict:
        """Whether the role's default can actually run: on disk, missing, or refused by its runtime."""
        default = settings[role]["model"]
        match = next((entry for entry in available if registry.matches(entry["name"], default)), None)
        total = sum(entry.get("size_bytes") or 0 for entry in available)
        runtime_reason = registry.unavailable_reason(role)
        if runtime_reason:
            return {"state": "incompatible", "reason": runtime_reason, "total_bytes": total}
        if match is not None:
            return {"state": "ready", "reason": None, "total_bytes": total}
        if _unsupported(role, default):
            return {"state": "incompatible",
                    "reason": f"{_unsupported(role, default)}; pick another default",
                    "total_bytes": total}
        if registry.runner(role) == "ollama":
            return {"state": "missing",
                    "reason": f"{default} is not installed; install it first",
                    "total_bytes": total}
        return {"state": "missing",
                "reason": f"{default} is not downloaded yet; loading downloads it first",
                "total_bytes": total}

    def recommended(role: str, available: list[dict]) -> list[dict]:
        """The role's curated models, its live default first, each with what stands in its way here."""
        rows = [{"model": model, "gb": size, "note": note} for model, size, note in registry.ROLE_SPECS[role]["recommended"]]
        default = settings[role]["model"]
        if not any(registry.matches(row["model"], default) for row in rows):
            rows.insert(0, {"model": default, "gb": None, "note": "current default"})
        for row in rows:
            match = next((entry for entry in available if registry.matches(entry["name"], row["model"])), None)
            size = match["size_bytes"] if match else int((row["gb"] or 0) * registry.GB)
            row["installed"] = match is not None
            row["blockers"] = registry.fit_blockers(size, on_disk=match is not None) if size else []
        return rows

    @app.get("/admin/models/status")
    async def admin_status() -> JSONResponse:
        """One summary per role: its spec, its default, what is on disk for it, whether the default can
        run, and the recommended models with what stands in their way on this machine."""
        roles = {}
        for role in registry.ROLES:
            try:
                available = await installed_for(role)
            except (httpx.HTTPError, *registry.HF_ERRORS):
                available = []
            spec = registry.ROLE_SPECS[role]
            roles[role] = {"default": settings[role]["model"], "runner": registry.runner(role),
                           "engine": spec["engine"], "kind": spec["kind"], "family": " or ".join(spec["family"]) or None,
                           "accepts": spec["accepts"], "example": spec["example"],
                           "recommended": recommended(role, available),
                           "installed": available, **setup_state(role, available)}
        return JSONResponse({"roles": roles})

    @app.get("/admin/models/downloads")
    async def admin_downloads() -> JSONResponse:
        """Every model download in flight, whatever started it, for progress bars."""
        return JSONResponse({"downloads": registry.snapshot(), "recent": registry.recent()})

    @app.get("/admin/models/check")
    async def admin_check(role: str, model: str = Query(..., min_length=1)) -> JSONResponse:
        """Can this role run this model id or page URL, before downloading anything?"""
        if role not in registry.ROLES:
            raise bad_role()
        name = registry.repo_name(role, model)
        reason = _unsupported(role, name)
        if reason:
            return JSONResponse({"role": role, "model": name, "supported": False, "reason": reason,
                                 "installed": False, "size_bytes": None, "blockers": []})
        try:
            available = await installed_for(role)
        except (httpx.HTTPError, *registry.HF_ERRORS):
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
                size = sum(part.size for part in await asyncio.to_thread(registry.bundle, role, name))
            except GatedRepoError:
                gated = True
            except registry.HF_ERRORS:
                size = None
        return JSONResponse({"role": role, "model": name, "supported": True, "reason": None,
                             "installed": False, "size_bytes": size, "gated": gated,
                             "accept_url": f"https://huggingface.co/{name}" if gated else None,
                             "blockers": registry.fit_blockers(size) if size else []})
