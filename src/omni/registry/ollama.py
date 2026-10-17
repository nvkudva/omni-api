"""Talking to the machine's Ollama: what it has, what a model can do, and load/unload/delete."""
import httpx

LOAD_KEEP_ALIVE = "10m"


async def installed(client: httpx.AsyncClient) -> list[dict]:
    response = await client.get("/api/tags", timeout=5.0)
    response.raise_for_status()
    models = []
    for model in response.json().get("models", []):
        if not isinstance(model, dict) or not model.get("name"):
            continue
        details = model.get("details") or {}
        models.append({
            "name": model["name"],
            "size_bytes": model.get("size") or 0,
            "parameters": details.get("parameter_size"),
            "quantization": details.get("quantization_level"),
        })
    return models


async def capabilities(client: httpx.AsyncClient, name: str) -> list[str]:
    """Ollama's own feature list for a model, e.g. ["completion", "vision"]."""
    try:
        response = await client.post("/api/show", json={"model": name}, timeout=5.0)
        response.raise_for_status()
        return response.json().get("capabilities", [])
    except (httpx.HTTPError, ValueError):
        return []


async def load(client: httpx.AsyncClient, role: str, model: str, keep_alive: str | int = LOAD_KEEP_ALIVE) -> None:
    """Load `model` into Ollama's memory, or with keep_alive=0 unload it."""
    if role == "embed":
        path, body = "/api/embed", {"model": model, "input": "", "keep_alive": keep_alive}
    else:
        path, body = "/api/generate", {"model": model, "keep_alive": keep_alive}
    response = await client.post(path, json=body, timeout=600.0)
    response.raise_for_status()


async def unload(client: httpx.AsyncClient, role: str, model: str) -> None:
    await load(client, role, model, keep_alive=0)


async def delete(client: httpx.AsyncClient, model: str) -> None:
    response = await client.request("DELETE", "/api/delete", json={"model": model}, timeout=30.0)
    response.raise_for_status()
