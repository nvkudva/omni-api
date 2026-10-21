"""Assembles the gateway app: load and validate config, build the shared context, mount the routes
and the playground, and start/stop the workers with the app's lifespan."""
import asyncio
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from omni import config, logs
from omni.errors import error_response, install_error_handlers
from omni.gateway import executions, residency, routes
from omni.gateway.context import build
from omni.registry import pulling

log = logging.getLogger("omni.gateway")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
PLAYGROUND_INDEX = STATIC_DIR / "app" / "index.html"
PLAYGROUND_PATHS = tuple(
    f"/{tab}" for tab in ("chat", "decide", "embed", "image", "video", "stt", "tts", "settings", "settings/server", "settings/models")
)
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

# the path a role answers on, so a request can be written into that role's own log
ROLE_PATHS = (
    ("/v1/chat/completions", "chat"),
    ("/v1/systemone", "decide"),
    ("/v1/embeddings", "embed"),
    ("/v1/audio/transcriptions", "stt"),
    ("/v1/audio", "tts"),
    ("/v1/images", "image"),
    ("/v1/videos", "video"),
)


def role_for(path: str) -> str | None:
    """The role a request path belongs to, or None when it is not a role's request."""
    for prefix, role in ROLE_PATHS:
        if path.startswith(prefix):
            return role
    return None


def _hostname(value: str) -> str:
    """The host part of a Host header or an Origin URL, without port or brackets."""
    netloc = urlsplit(value).netloc if "://" in value else value
    return (urlsplit(f"//{netloc}").hostname or "").lower()


def refusal(request: Request, allowed_hosts: set[str]) -> str | None:
    """Why a request must not reach the gateway, or None.

    Every route answers only loopback Host headers, so a DNS-rebound page cannot read anything.
    Admin changes also need a same-host Origin when a browser sends one, so a page the user
    happens to visit cannot stop the gateway or delete models with a simple cross-site POST.
    """
    host = request.headers.get("host", "")
    if _hostname(host) not in allowed_hosts:
        return f"host {host!r} is not a loopback address"
    origin = request.headers.get("origin")
    if (request.method in SAFE_METHODS or not request.url.path.startswith("/admin")
            or origin is None):
        return None
    if urlsplit(origin).netloc.lower() != host.lower():
        return f"origin {origin!r} may not change gateway state"
    return None


def create() -> FastAPI:
    """Build the app. Reads config (raising ``ConfigError`` on a bad one) and wires every route."""
    settings = config.load()
    ctx = build(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await ctx.pool.start()
        if ctx.keepwarm is not None:
            ctx.keepwarm.start()
        try:
            yield
        finally:
            if ctx.keepwarm is not None:
                await ctx.keepwarm.stop()
            await residency.unload_ollama_models(ctx)
            await executions.shutdown()
            await pulling.shutdown()
            await ctx.pool.stop()
            await asyncio.gather(*(client.aclose() for client in ctx.clients.values()))

    app = FastAPI(title="omni", lifespan=lifespan)
    install_error_handlers(app)

    allowed_hosts = LOOPBACK_HOSTS | {settings["gateway"]["host"].lower()}

    @app.middleware("http")
    async def loopback_only(request: Request, call_next):
        refused = refusal(request, allowed_hosts)
        if refused is not None:
            return error_response(403, refused, "forbidden")
        return await call_next(request)

    @app.middleware("http")
    async def role_activity(request: Request, call_next):
        """Write each request into the log of the role that answered it."""
        role = role_for(request.url.path)
        if role is None:
            return await call_next(request)
        started = time.perf_counter()
        response = await call_next(request)
        took = (time.perf_counter() - started) * 1000
        logs.note(role, f"{request.method} {request.url.path} {response.status_code} {took:.0f}ms")
        return response

    # the built playground ships inside the package; StaticFiles needs the directory to exist
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    for module in routes.MODULES:
        module.register(app, ctx)

    def playground() -> Response:
        if not PLAYGROUND_INDEX.is_file():
            return error_response(503, "the playground is not built; run `bun run build` in web/")
        return FileResponse(PLAYGROUND_INDEX)

    # the playground routes its tabs client-side; only these exact paths serve the page
    for path in ("/", *PLAYGROUND_PATHS, "/settings/models/{role}"):
        app.add_api_route(path, playground, methods=["GET"], include_in_schema=False)

    @app.get("/playground", include_in_schema=False)
    def old_playground() -> RedirectResponse:
        return RedirectResponse("/")

    log.info("gateway ready: %d roles, chat via %s", len(ctx.workers), ctx.chat_url)
    return app
