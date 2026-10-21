"""The /admin controls for the process itself: server status, restart and stop, runtime settings, logs."""
import copy

import httpx
import psutil
from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from omni import config, logs, registry, schemas, version
from omni.commands import lifecycle
from omni.errors import error_response
from omni.gateway.context import Context
from omni.gateway.http import json_object

# the settings a user may change at runtime, and the workers that read each one only when they start
TUNABLE = {
    ("gateway", "idle_unload_minutes"): registry.PYTHON_WORKERS,
    ("tts", "voice"): ("tts",),
    ("image", "steps"): ("image",),
    ("video", "frames"): ("video",),
    ("video", "fps"): ("video",),
}


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
        host, port = lifecycle.address(settings["gateway"])
        process = psutil.Process()
        ollama = await ollama_version()
        return {
            "gateway": {"pid": process.pid, "host": host, "port": port, "version": version() or "",
                        "started_at": process.create_time()},
            "ollama": {"url": ctx.chat_url, "version": ollama, "reachable": ollama is not None,
                       "managed": "chat" in pool.workers},
            "paths": {"home": str(config.HOME), "config": str(config.USER_CONFIG),
                      "log": str(config.LOG_DIR / "omni.log")},
            "tunables": {f"{section}.{key}": settings[section][key] for section, key in TUNABLE},
        }

    @app.post("/admin/server/{action}")
    async def admin_server_action(action: str) -> JSONResponse:
        if action not in ("restart", "stop"):
            return error_response(400, "action: restart or stop", "invalid_request_error")
        host, port = lifecycle.address(settings["gateway"])
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
            if (section, key) == ("tts", "voice") and value not in schemas.TTS_VOICES:
                raise config.ConfigError(f"tts.voice: one of {', '.join(schemas.TTS_VOICES)}")
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
        path = logs.path_for(None)
        return {"path": str(path), "lines": logs.tail(path, lines)}

    @app.get("/admin/logs/{role}")
    async def admin_role_log(role: str, lines: int = Query(500, ge=1, le=5000)) -> PlainTextResponse:
        if role != logs.OLLAMA_ENGINE and role not in config.ROLES:
            return error_response(404, f"no role named {role}", "not_found")
        path = logs.path_for(role)
        if not path.exists():
            hint = "Ollama has not written one" if role == logs.OLLAMA_ENGINE else "this runtime has not logged yet"
            return error_response(404, f"no log at {path} ({hint})", "not_found")
        return PlainTextResponse("\n".join(logs.tail(path, lines)) + "\n")
