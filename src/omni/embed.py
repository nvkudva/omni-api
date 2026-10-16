"""POST /v1/embeddings on top of Ollama /api/embed, with EmbeddingGemma's task prompts added per input."""
import httpx

MAX_INPUT_CHARS = 2000
INPUT_TYPES = ("query", "document")


def prompt(text: str, input_type: str) -> str:
    """EmbeddingGemma task prompts, the same ones Hyperlight indexes with."""
    text = text[:MAX_INPUT_CHARS]
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
    return texts


async def run(client: httpx.AsyncClient, model: str, payload: dict) -> dict:
    """OpenAI-shaped embeddings; raises ValueError for a malformed request."""
    input_type = payload.get("input_type", "document")
    if input_type not in INPUT_TYPES:
        raise ValueError(f"input_type: one of {', '.join(INPUT_TYPES)}")
    prompts = [prompt(text, input_type) for text in _texts(payload)]
    response = await client.post("/api/embed", json={"model": model, "input": prompts, "truncate": True})
    response.raise_for_status()
    reply = response.json()
    data = [{"object": "embedding", "index": index, "embedding": vector}
            for index, vector in enumerate(reply["embeddings"])]
    token_count = reply.get("prompt_eval_count", 0)
    return {"object": "list", "data": data, "model": model,
            "usage": {"prompt_tokens": token_count, "total_tokens": token_count}}
