"""The /admin controls for the process itself: server status, restart and stop, runtime settings, logs."""
import copy
import importlib.metadata

import httpx
import psutil
from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse

from omni import config, registry
from omni.commands import lifecycle
from omni.errors import error_response
from omni.gateway.context import Context
from omni.gateway.http import json_object

LOG_TAIL_BYTES = 256 * 1024
# the settings a user may change at runtime, and the workers that read each one only when they start
TUNABLE = {
    ("gateway", "idle_unload_minutes"): registry.PYTHON_WORKERS,
    ("tts", "voice"): ("tts",),
    ("image", "steps"): ("image",),
    ("video", "frames"): ("video",),
    ("video", "fps"): ("video",),
}


def _version() -> str:
    try:
        return importlib.metadata.version("omni-api")
    except importlib.metadata.PackageNotFoundError:
        return ""


def _tail(lines: int) -> list[str]:
    path = config.LOG_DIR / "omni.log"
    try:
        with path.open("rb") as file:
            file.seek(0, 2)
            file.seek(max(0, file.tell() - LOG_TAIL_BYTES))
            text = file.read().decode(errors="replace")
    except OSError:
        return []
    return text.splitlines()[-lines:]


def register(app: FastAPI, ctx: Context) -> None:
    settings, pool = ctx.settings, ctx.pool

    async def ollama_version() -> str | None:
        try:
            response = await ctx.clients["chat"].get("/api/version", timeout=2.0)
            return response.json().get("version", "")
        except (httpx.HTTPError, ValueError):
            return None

    @app.get("/admin/server")
    async def admin_server() -> dict:
        host, port = lifecycle.address(settings)
        process = psutil.Process()
        version = await ollama_version()
        return {
            "gateway": {"pid": process.pid, "host": host, "port": port, "version": _version(),
                        "started_at": process.create_time()},
            "ollama": {"url": ctx.chat_url, "version": version, "reachable": version is not None,
                       "managed": "chat" in pool.workers},
            "paths": {"home": str(config.HOME), "config": str(config.USER_CONFIG),
                      "log": str(config.LOG_DIR / "omni.log")},
            "tunables": {f"{section}.{key}": settings[section][key] for section, key in TUNABLE},
        }

    @app.post("/admin/server/{action}")
    async def admin_server_action(action: str) -> JSONResponse:
        if action not in ("restart", "stop"):
            return error_response(400, "action: restart or stop", "invalid_request_error")
        host, port = lifecycle.address(settings)
        lifecycle.relaunch(host, port, restart=action == "restart")
        return JSONResponse({"action": action}, status_code=202)

    @app.post("/admin/settings")
    async def admin_settings(request: Request) -> JSONResponse:
        payload = await json_object(request)
        section, key, value = payload.get("section"), payload.get("key"), payload.get("value")
        if (section, key) not in TUNABLE:
            names = ", ".join(f"{name}.{option}" for name, option in TUNABLE)
            return error_response(400, f"setting: one of {names}", "invalid_request_error")
        candidate = copy.deepcopy(settings)
        candidate[section][key] = value
        try:
            config.validate(candidate)
        except config.ConfigError as error:
            return error_response(400, str(error), "invalid_request_error")
        config.save_override(section, key, value)
        settings[section][key] = value
        restarted = [role for role in TUNABLE[(section, key)] if role in pool.workers]
        for role in restarted:
            await pool.restart(role)
        return JSONResponse({"setting": f"{section}.{key}", "value": value, "restarted": restarted})

    @app.get("/admin/logs")
    async def admin_logs(lines: int = Query(200, ge=1, le=1000)) -> dict:
        return {"path": str(config.LOG_DIR / "omni.log"), "lines": _tail(lines)}
