"""Text to image: POST /v1/images/generations on Qwen-Image-2.1 through diffusers (MPS, bf16)."""
import base64
import io
import json
import os
import time
from pathlib import Path

from fastapi import HTTPException

from omni import config, schemas, workers
from omni.registry import huggingface

settings = config.load()
model_name = settings["image"]["model"]
default_steps = settings["image"]["steps"]

_saved_sigmas: list[float] | None = None


def _find_gguf(repo: str) -> str | None:
    """The repo's 8-bit GGUF, when it ships one: a quantized checkpoint needs no extra config."""
    try:
        from huggingface_hub import HfApi

        return huggingface.preferred_gguf(HfApi().list_repo_files(repo))
    except Exception:  # noqa: BLE001 - an offline hub falls through to the plain load, which reports it
        return None


def _base_repo(repo: str) -> str:
    """The checkpoint a GGUF quantizes: the card's `base_model`, else the repo itself."""
    try:
        from huggingface_hub import HfApi

        base = (HfApi().model_info(repo).cardData or {}).get("base_model")
    except Exception:  # noqa: BLE001
        return repo
    if isinstance(base, list):
        base = base[0] if base else None
    return base if isinstance(base, str) and base else repo


def _source() -> tuple[str | None, str]:
    """(the GGUF file in `model`, or None; the repo that supplies the text encoder and VAE)."""
    file = settings["image"].get("file") or _find_gguf(model_name)
    if not file:
        return None, model_name
    return file, settings["image"].get("base") or _base_repo(model_name)


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


def _sigmas_for(steps: int) -> list[float] | None:
    if _saved_sigmas and len(_saved_sigmas) == steps:
        return _saved_sigmas
    return None


def _dequantize_vectors(model) -> None:
    """Replace the 1-D GGUF tensors (norms' scales) with plain ones.

    The pinned diffusers dequantizes only the 2-D weights it swaps for GGUF linears, so a norm's
    scale stays a byte-shaped `GGUFParameter` and every forward fails on its size.
    """
    import torch
    from diffusers.quantizers.gguf.utils import GGUFParameter, dequantize_gguf_tensor

    for module in model.modules():
        for name, parameter in list(module.named_parameters(recurse=False)):
            if isinstance(parameter, GGUFParameter) and parameter.dim() == 1:
                module._parameters[name] = torch.nn.Parameter(
                    dequantize_gguf_tensor(parameter).to(torch.bfloat16), requires_grad=False)


def _load():
    import torch
    from diffusers import QwenImage21Pipeline
    from huggingface_hub import hf_hub_download

    global _saved_sigmas
    file, base = _source()
    if file:
        from diffusers import GGUFQuantizationConfig, QwenImage21Transformer2DModel

        path = hf_hub_download(model_name, file)
        transformer = QwenImage21Transformer2DModel.from_single_file(
            path, config=base, subfolder="transformer",
            quantization_config=GGUFQuantizationConfig(compute_dtype=torch.bfloat16))
        _dequantize_vectors(transformer)
        pipe = QwenImage21Pipeline.from_pretrained(base, transformer=transformer,
                                                   torch_dtype=torch.bfloat16)
    else:
        pipe = QwenImage21Pipeline.from_pretrained(model_name, torch_dtype=torch.bfloat16)
    pipe = pipe.to("mps")
    _saved_sigmas = _read_saved_sigmas(base)
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
