"""POST /v1/embeddings on top of Ollama /api/embed, with EmbeddingGemma's task prompts added per input."""
import httpx

from omni.errors import UpstreamError
from omni.schemas import EMBED_INPUT_TYPES, EMBED_MAX_CHARS, EMBED_MAX_INPUTS


def prompt(text: str, input_type: str) -> str:
    """EmbeddingGemma task prompts, the same ones Hyperlight indexes with.

    Text is truncated to EMBED_MAX_CHARS on purpose: Hyperlight's in-process vectors were built with the
    same truncation, so changing it would invalidate its existing LanceDB index.
    """
    text = text[:EMBED_MAX_CHARS]
    if text.startswith(("task: ", "title: ")):
        return text
    if input_type == "document":
        return f"title: none | text: {text}"
    if text.startswith("code:"):
        return f"task: code retrieval | query: {text[len('code:'):].strip()}"
    return f"task: search result | query: {text}"


def _texts(payload: dict) -> list[str]:
    texts = payload.get("input")
    if isinstance(texts, str):
        texts = [texts]
    if not isinstance(texts, list) or not texts or not all(isinstance(text, str) and text for text in texts):
        raise ValueError("input: a non-empty string or list of non-empty strings")
    if len(texts) > EMBED_MAX_INPUTS:
        raise ValueError(f"input: at most {EMBED_MAX_INPUTS} strings per request")
    return texts


def _images(payload: dict) -> list[str] | None:
    """Base64 images from an input of {"image": data-URL-or-base64} items, or None when the input is text."""
    items = payload.get("input")
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list) or not any(isinstance(item, dict) for item in items):
        return None
    if len(items) > EMBED_MAX_INPUTS:
        raise ValueError(f"input: at most {EMBED_MAX_INPUTS} images per request")
    images = []
    for item in items:
        image = item.get("image") if isinstance(item, dict) else None
        if not isinstance(image, str) or not image:
            raise ValueError('input: images are objects like {"image": "<base64 or data URL>"}, not mixed with text')
        images.append(image.split(",", 1)[1] if image.startswith("data:") else image)
    return images


async def run(client: httpx.AsyncClient, model: str, payload: dict) -> dict:
    """OpenAI-shaped embeddings; raises ValueError for a malformed request."""
    input_type = payload.get("input_type", "document")
    if input_type not in EMBED_INPUT_TYPES:
        raise ValueError(f"input_type: one of {', '.join(EMBED_INPUT_TYPES)}")
    images = _images(payload)
    if images is not None:
        sent = [{"image": image} for image in images]
    else:
        sent = [prompt(text, input_type) for text in _texts(payload)]
    response = await client.post("/api/embed", json={"model": model, "input": sent, "truncate": True})
    response.raise_for_status()
    try:
        reply = response.json()
        vectors = reply["embeddings"]
    except (ValueError, KeyError, TypeError) as error:
        raise UpstreamError("ollama returned no embeddings") from error
    if not isinstance(vectors, list) or len(vectors) != len(sent):
        raise UpstreamError("ollama returned a different number of embeddings than inputs")
    data = [{"object": "embedding", "index": index, "embedding": vector}
            for index, vector in enumerate(vectors)]
    token_count = reply.get("prompt_eval_count", 0)
    return {"object": "list", "data": data, "model": model,
            "usage": {"prompt_tokens": token_count, "total_tokens": token_count}}
