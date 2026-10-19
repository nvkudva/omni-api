"""Text to image: POST /v1/images/generations on Qwen-Image-2.1 through diffusers (MPS, bf16)."""
import base64
import io
import json
import os
import time
from pathlib import Path

from fastapi import HTTPException

from omni import config, schemas, workers

settings = config.load()
model_name = settings["image"]["model"]
default_steps = settings["image"]["steps"]

_saved_sigmas: list[float] | None = None


def _read_saved_sigmas() -> list[float] | None:
    """The checkpoint's own denoising schedule, when it ships one (Turbo does as `sample_sigmas`)."""
    try:
        if os.path.isdir(model_name):
            path = Path(model_name) / "model_index.json"
        else:
            from huggingface_hub import hf_hub_download

            path = Path(hf_hub_download(model_name, "model_index.json"))
        sigmas = json.loads(path.read_text()).get("sample_sigmas")
        return [float(value) for value in sigmas] if sigmas else None
    except Exception:  # noqa: BLE001 - a missing schedule must fall back to the pipeline default
        return None


def _sigmas_for(steps: int) -> list[float] | None:
    if _saved_sigmas and len(_saved_sigmas) == steps:
        return _saved_sigmas
    return None


def _load():
    import torch
    from diffusers import QwenImage21Pipeline

    global _saved_sigmas
    pipe = QwenImage21Pipeline.from_pretrained(model_name, torch_dtype=torch.bfloat16).to("mps")
    _saved_sigmas = _read_saved_sigmas()
    return pipe


def _empty_cache() -> None:
    import torch

    torch.mps.empty_cache()


model = workers.Lazy(_load, config.idle_seconds(settings), _empty_cache)
executor = workers.ModelExecutor("image-model")
app = workers.app(model, model_executor=executor)


def _generate(req: schemas.ImageRequest, width: int, height: int, steps: int) -> list[str]:
    import torch

    generator = None if req.seed is None else torch.Generator("mps").manual_seed(req.seed)
    with model.use() as pipe:
        images = pipe(prompt=req.prompt, negative_prompt=req.negative_prompt, width=width, height=height,
                      num_inference_steps=steps, sigmas=_sigmas_for(steps),
                      num_images_per_prompt=req.n, generator=generator).images
    encoded = []
    for image in images:
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        encoded.append(base64.b64encode(buffer.getvalue()).decode())
    return encoded


@app.get("/health")
def health() -> dict:
    return {"ok": True, "model": model_name, "loaded": model.loaded}


@app.post("/v1/images/generations")
async def generations(req: schemas.ImageRequest) -> dict:
    if not schemas.model_matches(req.model, model_name, "image"):
        raise HTTPException(400, f"model: this worker serves only {model_name}")
    try:
        width, height = schemas.parse_size(req.size, max_side=schemas.IMAGE_MAX_SIDE, align=schemas.IMAGE_ALIGN)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    steps = req.steps if req.steps is not None else default_steps
    images = await executor.run(_generate, req, width, height, steps)
    return {"created": int(time.time()), "model": model_name, "size": f"{width}x{height}",
            "data": [{"b64_json": image, "revised_prompt": None} for image in images]}
