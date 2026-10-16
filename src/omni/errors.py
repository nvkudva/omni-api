"""One error shape for every endpoint: ``{"error": {"message", "type"}}``, as OpenAI uses.

Every FastAPI app in omni (the gateway and each worker) installs these handlers, so a
Pydantic validation failure, an ``HTTPException`` and an uncaught error all leave with the
same body instead of FastAPI's default ``{"detail": ...}``.
"""
import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("omni")

TYPE_BY_STATUS = {
    400: "invalid_request_error",
    404: "not_found",
    405: "invalid_request_error",
    409: "conflict",
    413: "payload_too_large",
    415: "invalid_request_error",
    422: "invalid_request_error",
    429: "rate_limit_error",
    500: "server_error",
    502: "upstream_error",
    503: "unavailable",
    504: "timeout",
}


class UpstreamError(RuntimeError):
    """A downstream engine (Ollama, a model runtime) answered in a shape we cannot use."""


def error_response(status: int, message: str, kind: str | None = None) -> JSONResponse:
    """The OpenAI error body for any status code."""
    return JSONResponse(
        {"error": {"message": str(message), "type": kind or TYPE_BY_STATUS.get(status, "server_error")}},
        status_code=status,
    )


def validation_message(error: RequestValidationError) -> str:
    """Pydantic's error list as one readable line."""
    parts = []
    for item in error.errors():
        where = ".".join(str(part) for part in item.get("loc", ()) if part not in ("body", "query"))
        message = item.get("msg", "invalid value")
        parts.append(f"{where}: {message}" if where else message)
    return "; ".join(parts) or "invalid request"


def install_error_handlers(app: FastAPI, *, expose_internal: bool = False) -> None:
    """Make every failure leave as the OpenAI shape, including FastAPI's own 422 and uncaught errors."""

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, error: StarletteHTTPException) -> JSONResponse:
        # registered on the starlette base so router-generated 404/405 use the same shape as our own
        return error_response(error.status_code, error.detail)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, error: RequestValidationError) -> JSONResponse:
        return error_response(422, validation_message(error))

    @app.exception_handler(Exception)
    async def _server_error(request: Request, error: Exception) -> JSONResponse:
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return error_response(500, f"{type(error).__name__}: {error}" if expose_internal else "internal error")
