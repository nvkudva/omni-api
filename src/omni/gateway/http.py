"""Reading request bodies the same way in every route."""
import json
from typing import Any

from fastapi import HTTPException, Request


async def json_body(request: Request) -> Any:
    """The request body parsed as JSON, or a 400 in the OpenAI shape."""
    try:
        return json.loads(await request.body())
    except json.JSONDecodeError as error:
        raise HTTPException(400, f"body must be JSON ({error})") from error


async def json_object(request: Request) -> dict:
    """The request body parsed as a JSON object, or a 400."""
    payload = await json_body(request)
    if not isinstance(payload, dict):
        raise HTTPException(400, "body must be a JSON object")
    return payload
