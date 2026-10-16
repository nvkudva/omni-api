import asyncio
import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask

from omni import config, decide, embed, models, supervisor

STATIC_DIR = Path(__file__).with_name("static")
FORWARDED_REQUEST_HEADERS = ("content-type", "accept")
FORWARDED_RESPONSE_HEADERS = ("content-type", "server-timing", "x-sample-rate")
PYTHON_WORKERS = ("stt", "tts", "image", "video")
ROLES = ("chat", "decide", "embed") + PYTHON_WORKERS


def _uds_client(role: str) -> httpx.AsyncClient:
    transport = httpx.AsyncHTTPTransport(uds=str(config.socket_path(role)))
    return httpx.AsyncClient(transport=transport, base_url="http://worker", timeout=None)


def _ollama_running(url: str) -> bool:
    try:
        return httpx.get(f"{url}/api/version", timeout=1.0).status_code == 200
    except httpx.HTTPError:
        return False


def _workers(settings: dict) -> tuple[list[supervisor.Worker], dict[str, httpx.AsyncClient], str]:
    """The children to supervise, a client per role, and where chat goes."""
    workers = []
    clients = {}

    # chat, decide and embed share one Ollama, on whatever address this machine configures (OLLAMA_HOST).
    chat_url = config.ollama_url()
    clients["chat"] = httpx.AsyncClient(base_url=chat_url, timeout=None)
    if not _ollama_running(chat_url):
        workers.append(supervisor.Worker(
            name="chat",
            argv=["ollama", "serve"],
            client=httpx.AsyncClient(base_url=chat_url),
            health_path="/api/version",
        ))

    for role in PYTHON_WORKERS:
        socket = config.socket_path(role)
        clients[role] = _uds_client(role)
        workers.append(supervisor.Worker(
            name=role,
            argv=[sys.executable, "-m", "uvicorn", f"omni.workers.{role}:app", "--uds", str(socket),
                  "--log-level", "warning"],
            client=_uds_client(role),
            health_path="/health",
            uds=socket,
        ))
    return workers, clients, chat_url


def _error(status: int, message: str, kind: str) -> JSONResponse:
    return JSONResponse({"error": {"message": message, "type": kind}}, status_code=status)


def create() -> FastAPI:
    settings = config.load()
    workers, clients, chat_url = _workers(settings)
    pool = supervisor.Supervisor(workers)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await pool.start()
        try:
            yield
        finally:
            await pool.stop()
            await asyncio.gather(*(client.aclose() for client in clients.values()))

    app = FastAPI(title="omni", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    async def forward(request: Request, role: str, path: str, body: bytes | None = None) -> Response:
        headers = {name: value for name, value in request.headers.items() if name in FORWARDED_REQUEST_HEADERS}
        content = await request.body() if body is None else body
        client = clients[role]
        upstream_request = client.build_request(request.method, path, content=content, headers=headers)
        try:
            upstream = await client.send(upstream_request, stream=True)
        except httpx.TransportError:
            state = pool.workers[role].status if role in pool.workers else "unreachable"
            return _error(503, f"{role} is not ready ({state}); GET /health shows progress", "unavailable")
        response_headers = {
            name: value for name, value in upstream.headers.items() if name in FORWARDED_RESPONSE_HEADERS
        }
        return StreamingResponse(
            upstream.aiter_raw(),
            status_code=upstream.status_code,
            headers=response_headers,
            background=BackgroundTask(upstream.aclose),
        )

    @app.post("/v1/chat/completions")
    async def chat(request: Request) -> Response:
        try:
            payload = json.loads(await request.body())
        except json.JSONDecodeError:
            return _error(400, "body must be JSON", "invalid_request_error")
        payload.setdefault("model", settings["chat"]["model"])
        return await forward(request, "chat", "/v1/chat/completions", json.dumps(payload).encode())

    async def on_ollama(request: Request, role: str, run) -> Response:
        """Parse the JSON body and answer it with `run(ollama_client, model, payload)`."""
        try:
            payload = json.loads(await request.body())
        except json.JSONDecodeError:
            return _error(400, "body must be JSON", "invalid_request_error")
        if not isinstance(payload, dict):
            return _error(400, "body must be a JSON object", "invalid_request_error")
        model = payload.get("model")
        if not model or model == role:
            model = settings[role]["model"]
        try:
            return JSONResponse(await run(clients["chat"], model, payload))
        except ValueError as error:
            return _error(400, str(error), "invalid_request_error")
        except httpx.TransportError:
            state = pool.workers["chat"].status if "chat" in pool.workers else "unreachable"
            return _error(503, f"ollama is not ready ({state}); GET /health shows progress", "unavailable")
        except httpx.HTTPStatusError as error:
            return _error(error.response.status_code, f"ollama: {error.response.text}", "upstream_error")

    @app.post("/v1/systemone")
    async def systemone(request: Request) -> Response:
        return await on_ollama(request, "decide", decide.run)

    @app.post("/v1/embeddings")
    async def embeddings(request: Request) -> Response:
        return await on_ollama(request, "embed", embed.run)

    @app.post("/v1/images/generations")
    async def images(request: Request) -> Response:
        return await forward(request, "image", "/v1/images/generations")

    @app.post("/v1/videos")
    async def create_video(request: Request) -> Response:
        return await forward(request, "video", "/v1/videos")

    @app.get("/v1/videos/{video_id}")
    async def video_status(request: Request, video_id: str) -> Response:
        return await forward(request, "video", f"/v1/videos/{video_id}")

    @app.get("/v1/videos/{video_id}/content")
    async def video_content(request: Request, video_id: str) -> Response:
        return await forward(request, "video", f"/v1/videos/{video_id}/content")

    @app.post("/v1/audio/transcriptions")
    async def transcriptions(request: Request) -> Response:
        return await forward(request, "stt", "/v1/audio/transcriptions")

    @app.post("/v1/audio/speech")
    async def speech(request: Request) -> Response:
        return await forward(request, "tts", "/v1/audio/speech")

    @app.get("/v1/audio/voices")
    async def voices(request: Request) -> Response:
        return await forward(request, "tts", "/v1/audio/voices")

    @app.get("/v1/models")
    async def list_models() -> dict:
        names = await _chat_models(clients["chat"])
        found = await asyncio.gather(*(models.capabilities(clients["chat"], name) for name in names))
        data = [{"id": name, "object": "model", "owned_by": "omni", "role": "chat", "capabilities": capabilities}
                for name, capabilities in zip(names, found)]
        for role in ("decide", "embed") + PYTHON_WORKERS:
            data.append({"id": settings[role]["model"], "object": "model", "owned_by": "omni", "role": role})
        defaults = {role: settings[role]["model"] for role in ROLES}
        return {"object": "list", "data": data, "defaults": defaults}

    async def installed(role: str) -> list[dict]:
        if models.runner(role) == "ollama":
            return await models.ollama_installed(clients["chat"])
        return await asyncio.to_thread(models.hf_installed)

    async def model_request(request: Request) -> tuple[str, str]:
        """The role and model a /admin/models request names; raises ValueError when either is bad."""
        try:
            payload = json.loads(await request.body())
        except json.JSONDecodeError:
            raise ValueError("body must be JSON")
        role, model = payload.get("role"), str(payload.get("model", "")).strip()
        if role not in ROLES:
            raise ValueError(f"role: one of {', '.join(ROLES)}")
        if not model:
            raise ValueError("model: required")
        return role, model

    @app.get("/admin/models")
    async def admin_models() -> Response:
        try:
            ollama = await models.ollama_installed(clients["chat"])
        except (httpx.HTTPError, ValueError):
            ollama = []
        huggingface = await asyncio.to_thread(models.hf_installed)
        roles = {role: {"default": settings[role]["model"], "runner": models.runner(role)} for role in ROLES}
        return JSONResponse({"roles": roles, "installed": {"ollama": ollama, "huggingface": huggingface}})

    @app.post("/admin/models/pull")
    async def admin_pull(request: Request) -> Response:
        try:
            role, model = await model_request(request)
        except ValueError as error:
            return _error(400, str(error), "invalid_request_error")

        async def events():
            async for event in models.pull(clients["chat"], role, model):
                yield json.dumps(event) + "\n"

        return StreamingResponse(events(), media_type="application/x-ndjson")

    @app.post("/admin/models/delete")
    async def admin_delete(request: Request) -> Response:
        try:
            role, model = await model_request(request)
        except ValueError as error:
            return _error(400, str(error), "invalid_request_error")
        users = [name for name in ROLES if settings[name]["model"] == model]
        if users:
            return _error(409, f"{model} is the default for {', '.join(users)}; switch that first", "conflict")
        try:
            if models.runner(role) == "ollama":
                response = await clients["chat"].request("DELETE", "/api/delete", json={"model": model})
                response.raise_for_status()
            else:
                await asyncio.to_thread(models.hf_delete, model)
        except httpx.HTTPError as error:
            return _error(502, f"ollama: {error}", "upstream_error")
        return JSONResponse({"deleted": model})

    @app.post("/admin/models/default")
    async def admin_default(request: Request) -> Response:
        try:
            role, model = await model_request(request)
            available = await installed(role)
        except ValueError as error:
            return _error(400, str(error), "invalid_request_error")
        except httpx.HTTPError as error:
            return _error(503, f"ollama: {error}", "unavailable")
        match = next((entry for entry in available if entry["name"] in (model, f"{model}:latest")), None)
        if match is None:
            return _error(400, f"{model} is not installed; install it first", "invalid_request_error")
        config.save_override(role, "model", model)
        settings[role]["model"] = model
        if role in PYTHON_WORKERS:
            await pool.restart(role)
        return JSONResponse({"role": role, "model": model, "warnings": models.fit_warnings(match["size_bytes"])})

    @app.get("/admin/models/search")
    async def admin_search(role: str, q: str = "", limit: int = 20) -> Response:
        if role not in ROLES:
            return _error(400, f"role: one of {', '.join(ROLES)}", "invalid_request_error")
        try:
            results = await asyncio.to_thread(models.search, role, q, min(limit, 50))
        except (httpx.HTTPError, OSError) as error:
            return _error(502, f"huggingface: {error}", "upstream_error")
        return JSONResponse({"role": role, "results": results})

    async def residency_request(request: Request) -> tuple[str, str, bool]:
        """The role, model and force flag a load/unload names; the model defaults to the role's default."""
        try:
            payload = json.loads(await request.body())
        except json.JSONDecodeError:
            raise ValueError("body must be JSON")
        role = payload.get("role")
        if role not in ROLES:
            raise ValueError(f"role: one of {', '.join(ROLES)}")
        model = str(payload.get("model") or settings[role]["model"]).strip()
        if role in PYTHON_WORKERS and model != settings[role]["model"]:
            raise ValueError(f"the {role} worker serves only its default, {settings[role]['model']}; make {model} the default first")
        return role, model, bool(payload.get("force"))

    async def load_warnings(role: str, model: str) -> list[str]:
        try:
            available = await installed(role)
        except httpx.HTTPError:
            available = []
        match = next((entry for entry in available if entry["name"] in (model, f"{model}:latest")), None)
        size_warnings = models.fit_warnings(match["size_bytes"]) if match else [f"{model} is not downloaded yet; loading downloads it first"]
        return models.overload_warnings() + size_warnings

    async def set_residency(role: str, model: str, load: bool) -> None:
        if models.runner(role) == "ollama":
            if load:
                await models.ollama_load(clients["chat"], role, model)
            else:
                await models.ollama_unload(clients["chat"], role, model)
            return
        response = await clients[role].post("/load" if load else "/unload", timeout=None)
        response.raise_for_status()

    async def residency(request: Request, load: bool) -> Response:
        try:
            role, model, force = await residency_request(request)
        except ValueError as error:
            return _error(400, str(error), "invalid_request_error")
        warnings = await load_warnings(role, model) if load else []
        if warnings and not force:
            return _error(409, "; ".join(warnings) + ". Send force: true to load anyway.", "overload")
        try:
            await set_residency(role, model, load)
        except httpx.TransportError:
            state = pool.workers[role].status if role in pool.workers else "unreachable"
            return _error(503, f"{role} is not running ({state}); start it first", "unavailable")
        except httpx.HTTPStatusError as error:
            return _error(502, f"{role}: {error.response.text}", "upstream_error")
        return JSONResponse({"role": role, "model": model, "loaded": load, "warnings": warnings})

    @app.post("/admin/models/load")
    async def admin_load(request: Request) -> Response:
        return await residency(request, load=True)

    @app.post("/admin/models/unload")
    async def admin_unload(request: Request) -> Response:
        return await residency(request, load=False)

    @app.post("/admin/workers/{name}/{action}")
    async def admin_worker(name: str, action: str) -> Response:
        if name not in pool.workers:
            if name == "chat":
                return _error(409, f"ollama runs outside omni at {chat_url}; manage it there", "conflict")
            return _error(404, f"no worker named {name}", "not_found")
        if action in ("start", "restart"):
            await pool.restart(name)
        elif action == "stop":
            await pool.hold(name)
        else:
            return _error(404, "action: start, stop or restart", "not_found")
        return JSONResponse({"worker": name, "status": pool.workers[name].status})

    @app.get("/admin/memory")
    async def admin_memory() -> dict:
        return await models.memory(clients["chat"], pool.status())

    @app.get("/health")
    async def health() -> dict:
        statuses = pool.status()
        if "chat" not in statuses:
            statuses["chat"] = {"status": "external", "url": chat_url}
        ready = all(entry["status"] in ("ready", "external") for entry in statuses.values())
        return {"ok": ready, "workers": statuses}

    @app.get("/")
    def root() -> RedirectResponse:
        return RedirectResponse("/playground")

    @app.get("/playground")
    def playground() -> FileResponse:
        return FileResponse(STATIC_DIR / "app" / "index.html")

    return app


async def _chat_models(client: httpx.AsyncClient) -> list[str]:
    try:
        response = await client.get("/api/tags", timeout=2.0)
        return [model["name"] for model in response.json().get("models", [])]
    except (httpx.HTTPError, ValueError):
        return []
