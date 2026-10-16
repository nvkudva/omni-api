"""Assembles the gateway app: load and validate config, build the shared context, mount the routes
and the playground, and start/stop the workers with the app's lifespan."""
import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from omni import config
from omni.errors import error_response, install_error_handlers
from omni.gateway import routes
from omni.gateway.context import build

log = logging.getLogger("omni.gateway")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
PLAYGROUND_INDEX = STATIC_DIR / "app" / "index.html"


def create() -> FastAPI:
    """Build the app. Reads config (raising ``ConfigError`` on a bad one) and wires every route."""
    settings = config.load()
    ctx = build(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await ctx.pool.start()
        try:
            yield
        finally:
            await ctx.pool.stop()
            await asyncio.gather(*(client.aclose() for client in ctx.clients.values()))

    app = FastAPI(title="omni", lifespan=lifespan)
    install_error_handlers(app)
    # the built playground ships inside the package; StaticFiles needs the directory to exist
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    for module in routes.MODULES:
        module.register(app, ctx)

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse("/playground")

    @app.get("/playground", include_in_schema=False)
    def playground() -> Response:
        if not PLAYGROUND_INDEX.is_file():
            return error_response(503, "the playground is not built; run `bun run build` in web/")
        return FileResponse(PLAYGROUND_INDEX)

    log.info("gateway ready: %d roles, chat via %s", len(ctx.workers), ctx.chat_url)
    return app
