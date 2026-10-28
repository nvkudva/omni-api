"""Text to image: POST /v1/images/generations through diffusers on MPS: Qwen-Image-2.1 (bf16), Stable Diffusion 1.5 or SDXL-Lightning (fp16)."""
import base64
import io
import json
import os
import time
from pathlib import Path

from fastapi import HTTPException

from omni import config, schemas, workers
from omni.registry import roles
from omni.workers import gguf

settings = config.load()
model_name = settings["image"]["model"]
default_steps = settings["image"]["steps"]
family = roles.backend("image", model_name)
# The sampler each non-Qwen family was trained for. Qwen's step count is the `image.steps` setting.
SAMPLING = {
    "stable-diffusion-v1-5": {"steps": 25, "guidance_scale": 7.5},
    "sdxl-lightning": {"steps": 4, "guidance_scale": 0.0},
}

_saved_sigmas: list[float] | None = None


def _read_saved_sigmas(repo: str) -> list[float] | None:
    """The checkpoint's own denoising schedule, when it ships one (Turbo does as `sample_sigmas`)."""
    try:
        if os.path.isdir(repo):
            path = Path(repo) / "model_index.json"
        else:
            from huggingface_hub import hf_hub_download

            path = Path(hf_hub_download(repo, "model_index.json"))
        sigmas = json.loads(path.read_text()).get("sample_sigmas")
        return [float(value) for value in sigmas] if sigmas else None
    except Exception:  # noqa: BLE001 - a missing schedule must fall back to the pipeline default
        return None


def _default_steps() -> int:
    return SAMPLING[family]["steps"] if family in SAMPLING else default_steps


def _sigmas_for(steps: int) -> list[float] | None:
    if _saved_sigmas and len(_saved_sigmas) == steps:
        return _saved_sigmas
    return None


def _load_stable_diffusion():
    import torch
    from diffusers import StableDiffusionPipeline

    # no safety checker: it is a 1.2 GB download this worker never reads
    return StableDiffusionPipeline.from_pretrained(
        model_name, torch_dtype=torch.float16, variant="fp16", safety_checker=None, requires_safety_checker=False)


def _load_lightning():
    import torch
    from diffusers import EulerDiscreteScheduler, StableDiffusionXLPipeline, UNet2DConditionModel
    from safetensors.torch import load_file

    weights, base = gguf.checkpoint("image", model_name)
    unet = UNet2DConditionModel.from_config(base, subfolder="unet").to(torch.float16)
    unet.load_state_dict(load_file(weights))
    pipe = StableDiffusionXLPipeline.from_pretrained(base, unet=unet, torch_dtype=torch.float16, variant="fp16")
    pipe.scheduler = EulerDiscreteScheduler.from_config(pipe.scheduler.config, timestep_spacing="trailing")
    return pipe


def _load_qwen():
    import torch
    from diffusers import QwenImage21Pipeline

    global _saved_sigmas
    file, base = gguf.source("image", model_name)
    if file:
        from diffusers import GGUFQuantizationConfig, QwenImage21Transformer2DModel

        transformer = QwenImage21Transformer2DModel.from_single_file(
            file, config=base, subfolder="transformer",
            quantization_config=GGUFQuantizationConfig(compute_dtype=torch.bfloat16))
        gguf.dequantize_vectors(transformer)
        pipe = QwenImage21Pipeline.from_pretrained(base, transformer=transformer,
                                                   torch_dtype=torch.bfloat16)
    else:
        pipe = QwenImage21Pipeline.from_pretrained(model_name, torch_dtype=torch.bfloat16)
    _saved_sigmas = _read_saved_sigmas(base or model_name)
    return pipe


def _load():
    loaders = {"stable-diffusion-v1-5": _load_stable_diffusion, "sdxl-lightning": _load_lightning}
    return loaders.get(family, _load_qwen)().to("mps")


def _empty_cache() -> None:
    import torch

    torch.mps.empty_cache()


model = workers.Lazy(_load, _empty_cache)
executor = workers.ModelExecutor("image-model", max_pending=2)
app = workers.app(model, model_executor=executor)


def _generate(req: schemas.ImageRequest, width: int, height: int, steps: int) -> list[str]:
    import torch

    generator = None if req.seed is None else torch.Generator("mps").manual_seed(req.seed)
    with model.use() as pipe:
        options = {"sigmas": _sigmas_for(steps)} if family not in SAMPLING else {"guidance_scale": SAMPLING[family]["guidance_scale"]}
        images = pipe(prompt=req.prompt, negative_prompt=req.negative_prompt, width=width, height=height,
                      num_inference_steps=steps, num_images_per_prompt=req.n, generator=generator, **options).images
    encoded = []
    for image in images:
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        encoded.append(base64.b64encode(buffer.getvalue()).decode())
    return encoded


@app.get("/health")
def health() -> dict:
    return {"ok": True, "model": model_name, "loaded": model.loaded, "busy": executor.pending}


@app.post("/v1/images/generations")
async def generations(req: schemas.ImageRequest) -> dict:
    if not schemas.model_matches(req.model, model_name, "image"):
        raise HTTPException(400, f"model: this worker serves only {model_name}")
    try:
        width, height = schemas.parse_size(req.size, max_side=schemas.IMAGE_MAX_SIDE, align=schemas.IMAGE_ALIGN)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    steps = req.steps if req.steps is not None else _default_steps()
    images = await executor.run_request(_generate, req, width, height, steps)
    return {"created": int(time.time()), "model": model_name, "size": f"{width}x{height}",
            "data": [{"b64_json": image, "revised_prompt": None} for image in images]}
