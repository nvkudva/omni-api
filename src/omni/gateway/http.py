"""Reading request bodies the same way in every route."""
import json
from typing import Any

from fastapi import HTTPException, Request
from starlette.requests import ClientDisconnect

from omni import registry

MAX_JSON_BYTES = 8 * 1024 * 1024


async def body_chunks(request: Request):
    """The request body, chunk by chunk.

    A client that hangs up mid-body is a quiet 400, not a server failure: the request
    is over, and the client will never read the answer.
    """
    try:
        async for chunk in request.stream():
            yield chunk
    except ClientDisconnect as error:
        raise HTTPException(400, "client closed the request") from error


async def json_body(request: Request) -> Any:
    """The request body parsed as JSON, or a 400 in the OpenAI shape."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_JSON_BYTES:
        raise HTTPException(413, "body: larger than 8 MB")
    chunks, total = [], 0
    async for chunk in body_chunks(request):
        total += len(chunk)
        if total > MAX_JSON_BYTES:
            raise HTTPException(413, "body: larger than 8 MB")
        chunks.append(chunk)
    try:
        return json.loads(b"".join(chunks))
    except json.JSONDecodeError as error:
        raise HTTPException(400, f"body must be JSON ({error})") from error


def bad_role() -> HTTPException:
    return HTTPException(400, f"role: one of {', '.join(registry.ROLES)}")


def optional_model(payload: dict) -> str | None:
    value = payload.get("model")
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise HTTPException(400, "model: must be a non-empty string")
    return value.strip()


async def json_object(request: Request) -> dict:
    """The request body parsed as a JSON object, or a 400."""
    payload = await json_body(request)
    if not isinstance(payload, dict):
        raise HTTPException(400, "body must be a JSON object")
    return payload
