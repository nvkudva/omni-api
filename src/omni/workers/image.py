import asyncio
import base64
import io
import time

from fastapi import HTTPException
from pydantic import BaseModel, Field

from omni import config, workers

MAX_SIDE = 2048

settings = config.load()
model_name = settings["image"]["model"]


def _load():
    import torch
    from diffusers import QwenImage21Pipeline

    return QwenImage21Pipeline.from_pretrained(model_name, torch_dtype=torch.bfloat16).to("mps")


def _empty_cache() -> None:
    import torch

    torch.mps.empty_cache()


model = workers.Lazy(_load, config.idle_seconds(settings), _empty_cache)
app = workers.app(model)


class ImageRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    model: str | None = None
    n: int = Field(1, ge=1, le=4)
    size: str = Field("1024x1024", pattern=r"^\d{3,4}x\d{3,4}$")
    negative_prompt: str | None = None
    steps: int = Field(settings["image"]["steps"], ge=1, le=100)
    seed: int | None = None
    response_format: str = Field("b64_json", pattern="^b64_json$")


def _dimensions(size: str) -> tuple[int, int]:
    width, height = (int(side) for side in size.split("x"))
    if not (256 <= width <= MAX_SIDE and 256 <= height <= MAX_SIDE):
        raise HTTPException(400, f"size: each side between 256 and {MAX_SIDE}")
    # the transformer patches latents in 16-pixel blocks
    return width - width % 16, height - height % 16


def _generate(req: ImageRequest, width: int, height: int) -> list[str]:
    import torch

    generator = None if req.seed is None else torch.Generator("mps").manual_seed(req.seed)
    with model.use() as pipe:
        images = pipe(prompt=req.prompt, negative_prompt=req.negative_prompt, width=width, height=height,
                      num_inference_steps=req.steps, num_images_per_prompt=req.n, generator=generator).images
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
async def generations(req: ImageRequest) -> dict:
    width, height = _dimensions(req.size)
    images = await asyncio.to_thread(_generate, req, width, height)
    return {"created": int(time.time()), "model": model_name,
            "data": [{"b64_json": image, "revised_prompt": req.prompt} for image in images]}
