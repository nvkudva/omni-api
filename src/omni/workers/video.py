"""Text/image-to-video through diffusers (Wan 2.2 TI2V, or LTX-Video), as OpenAI-style jobs: create, poll, fetch the mp4.

Jobs are capped, expire on a TTL and their mp4s are deleted, so a long session cannot grow without bound.
"""
import asyncio
import inspect
import json
import logging
import re
import subprocess
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from PIL import Image

from omni import config, schemas, workers
from omni.registry import pulling
from omni.workers import gguf, pictures

log = logging.getLogger("omni.worker.video")

MAX_QUEUE = 8
JOB_TTL_SECONDS = 3600
SWEEP_SECONDS = 60
JOB_ID_PATTERN = re.compile(r"^video_[0-9a-f]{16}$")
# Per model family: the diffusers classes, and the sampler defaults its authors recommend
# (LTX-Video's distilled checkpoints run few steps with guidance off).
FAMILIES = {
    "wan": {"text": "WanPipeline", "image": "WanImageToVideoPipeline", "transformer": "WanTransformer3DModel",
            "steps": 30, "guidance": 5.0},
    "ltx": {"text": "LTXPipeline", "image": "LTXImageToVideoPipeline", "transformer": "LTXVideoTransformer3DModel",
            "steps": 8, "guidance": 1.0},
}

settings = config.load()
model_name = settings["video"]["model"]
fps = settings["video"]["fps"]
config.ensure_dirs()


def _family() -> dict:
    for name, family in FAMILIES.items():
        if name in model_name.lower():
            return family
    raise config.ConfigError(f"video.model {model_name!r} is neither a Wan nor an LTX-Video repo")


def _source() -> tuple[str | None, str]:
    """(the GGUF file in `model`, or None; the diffusers repo that supplies the text encoder and VAE)."""
    file = settings["video"].get("file") or gguf.find_file(model_name)
    if not file:
        return None, model_name
    return file, settings["video"].get("base") or gguf.base_repo(model_name)


def _components(repo: str) -> str:
    """Download everything of a diffusers repo except the transformer weights, and return the folder.

    The GGUF replaces the transformer, so its 20 GB of bf16 shards are never read; the repo's
    own single-file checkpoints (LTX-Video ships several) are skipped the same way.
    """
    from huggingface_hub import hf_hub_download, snapshot_download

    try:
        index = json.loads(Path(hf_hub_download(repo, "model_index.json")).read_text())
    except Exception:  # noqa: BLE001 - a card's base_model may name the original, not the diffusers conversion
        repo = f"{repo}-Diffusers"
        index = json.loads(Path(hf_hub_download(repo, "model_index.json")).read_text())
    others = [name for name, value in index.items() if isinstance(value, list) and value[0] and name != "transformer"]
    allowed = ["model_index.json", "transformer/config.json", *(f"{name}/*" for name in others)]
    return snapshot_download(repo, allow_patterns=allowed)


def _device() -> str:
    import torch

    if torch.backends.mps.is_available():
        return "mps"
    return "cuda" if torch.cuda.is_available() else "cpu"


def _load():
    import diffusers
    import torch
    from huggingface_hub import hf_hub_download

    family = _family()
    pipeline_class = getattr(diffusers, family["text"])
    file, base = _source()
    if file:
        folder = _components(base)
        transformer = getattr(diffusers, family["transformer"]).from_single_file(
            hf_hub_download(model_name, file), config=folder, subfolder="transformer",
            quantization_config=diffusers.GGUFQuantizationConfig(compute_dtype=torch.bfloat16))
        gguf.dequantize_vectors(transformer)
        extra = {"vae": diffusers.AutoencoderKLWan.from_pretrained(folder, subfolder="vae", torch_dtype=torch.float32)} \
            if family is FAMILIES["wan"] else {}
        pipe = pipeline_class.from_pretrained(folder, transformer=transformer, torch_dtype=torch.bfloat16, **extra)
    else:
        pipe = pipeline_class.from_pretrained(model_name, torch_dtype=torch.bfloat16)
    pipe.vae.enable_tiling()
    return pipe.to(_device())


def _clear_cache() -> None:
    import torch

    if torch.backends.mps.is_available():
        torch.mps.empty_cache()


model = workers.Lazy(_load, config.idle_seconds(settings), _clear_cache)
executor = workers.ModelExecutor("video-model")
jobs: dict[str, dict] = {}
queue: asyncio.Queue[tuple[str, schemas.VideoRequest, int, int]] = asyncio.Queue(maxsize=MAX_QUEUE)


def _frames(seconds: float) -> int:
    # both VAEs pack frames in fours (Wan) or eights (LTX) after the first; 8n + 1 suits both
    return max(1, round(seconds * fps / 8)) * 8 + 1


def _public(job_id: str) -> dict:
    return {key: value for key, value in list(jobs[job_id].items()) if key != "path"}


def _live(job_id: str, job: dict) -> bool:
    """False once the sweeper has expired the job or dropped it, so late results are discarded."""
    return jobs.get(job_id) is job and job["status"] != "failed"


def _render(pipe, req: schemas.VideoRequest, width: int, height: int, image_path: Path | None) -> np.ndarray:
    """The frames as floats in [0, 1], shaped (frames, height, width, 3)."""
    import diffusers
    import torch

    family = _family()
    steps = settings["video"].get("steps", family["steps"])
    options = {"prompt": req.prompt, "negative_prompt": req.negative_prompt, "height": height, "width": width,
               "num_frames": _frames(req.seconds), "num_inference_steps": steps,
               "guidance_scale": family["guidance"], "generator": torch.Generator("cpu").manual_seed(req.seed),
               "output_type": "np"}
    if image_path is not None:
        # not from_pipe: it casts the pipeline to float32, which a quantized transformer refuses
        image_class = getattr(diffusers, family["image"])
        accepted = inspect.signature(image_class.__init__).parameters
        settings_kept = {key: value for key, value in pipe.config.items() if key in accepted and key not in pipe.components}
        pipe = image_class(**pipe.components, **settings_kept)
        with Image.open(image_path) as picture:
            options["image"] = picture.convert("RGB")
    return pipe(**options).frames[0]


def _write_mp4(frames: np.ndarray, path: Path) -> None:
    height, width = frames.shape[1:3]
    pixels = (np.clip(frames, 0.0, 1.0) * 255).round().astype(np.uint8)
    command = ["ffmpeg", "-nostdin", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
               "-s", f"{width}x{height}", "-r", str(fps), "-i", "-", "-c:v", "libx264",
               "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(path)]
    done = subprocess.run(command, input=pixels.tobytes(), capture_output=True, check=False)
    if done.returncode != 0:
        raise RuntimeError(f"ffmpeg could not encode the video: {done.stderr.decode(errors='replace').strip()[-300:]}")


def _generate(job_id: str, req: schemas.VideoRequest, width: int, height: int) -> None:
    job = jobs.get(job_id)
    if job is None or not _live(job_id, job):
        return
    path = config.VIDEO_DIR / f"{job_id}.mp4"
    image_path = config.VIDEO_DIR / f"{job_id}.png" if req.image else None
    try:
        if image_path is not None:
            image_path.write_bytes(pictures.decode(req.image))
        with model.use() as pipe:
            if not _live(job_id, job):
                return
            job["status"] = "in_progress"
            frames = _render(pipe, req, width, height, image_path)
        _write_mp4(frames, path)
        if _live(job_id, job):
            job.update(status="completed", completed_at=int(time.time()), path=str(path))
        else:
            path.unlink(missing_ok=True)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    finally:
        if image_path is not None:
            image_path.unlink(missing_ok=True)


def job_error(error: Exception) -> dict:
    """The job's public error: a gated repo reads as an instruction, not a raw upstream trace."""
    if pulling.is_gated(error):
        event = pulling.gated_event(model_name)
        return {"message": event["error"], "type": "gated", "code": "gated", "accept_url": event["accept_url"]}
    return {"message": f"{type(error).__name__}: {error}", "type": "server_error"}


async def _run_jobs() -> None:
    while True:
        job_id, req, width, height = await queue.get()
        try:
            await executor.run(_generate, job_id, req, width, height)
        except Exception as error:  # one bad generation must not stop the queue
            log.exception("video %s failed", job_id)
            job = jobs.get(job_id)
            if job is not None:
                job.update(status="failed", failed_at=int(time.time()), error=job_error(error))
        finally:
            queue.task_done()


def _purge_stale_files() -> None:
    """Jobs live in memory only, so files left by a previous run can never be fetched or swept."""
    cutoff = time.time() - JOB_TTL_SECONDS
    for pattern in ("*.mp4", "*.png"):
        for file in config.VIDEO_DIR.glob(pattern):
            try:
                if file.stat().st_mtime < cutoff:
                    file.unlink(missing_ok=True)
            except OSError:
                log.warning("could not remove stale %s", file)


async def _sweep_jobs() -> None:
    """Forget finished jobs after a while and delete their mp4s."""
    while True:
        await asyncio.sleep(SWEEP_SECONDS)
        cutoff = time.time() - JOB_TTL_SECONDS
        for job_id, job in list(jobs.items()):
            if job["status"] == "in_progress" and time.time() - job.get("created_at", time.time()) > JOB_TTL_SECONDS:
                job.update(status="failed", failed_at=int(time.time()),
                           error={"message": "generation timed out", "type": "timeout"})
                continue
            if job["status"] not in ("completed", "failed"):
                continue
            finished = job.get("completed_at") or job.get("failed_at") or job.get("created_at")
            if finished and finished < cutoff:
                if job.get("path"):
                    Path(job["path"]).unlink(missing_ok=True)
                jobs.pop(job_id, None)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await asyncio.to_thread(_purge_stale_files)
    runner = asyncio.create_task(_run_jobs())
    sweeper = asyncio.create_task(_sweep_jobs())
    try:
        yield
    finally:
        runner.cancel()
        sweeper.cancel()
        await asyncio.gather(runner, sweeper, return_exceptions=True)


app = workers.app(model, lifespan=lifespan, model_executor=executor)


@app.get("/health")
def health() -> dict:
    return {"ok": True, "model": model_name, "loaded": model.loaded, "queued": queue.qsize(),
            "busy": executor.pending + queue.qsize()}


@app.post("/v1/videos")
async def create(req: schemas.VideoRequest) -> dict:
    if not schemas.model_matches(req.model, model_name, "video"):
        raise HTTPException(400, f"model: this worker serves only {model_name}")
    try:
        width, height = schemas.parse_size(req.size, max_side=schemas.VIDEO_MAX_SIDE, align=schemas.VIDEO_ALIGN)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    if req.image:
        await asyncio.to_thread(pictures.decode, req.image)
    job_id = f"video_{uuid.uuid4().hex[:16]}"
    frames = _frames(req.seconds)
    jobs[job_id] = {"id": job_id, "object": "video", "model": model_name, "status": "queued",
                    "created_at": int(time.time()), "size": f"{width}x{height}",
                    "seconds": round(frames / fps, 2), "prompt": req.prompt}
    try:
        queue.put_nowait((job_id, req, width, height))
    except asyncio.QueueFull:
        jobs.pop(job_id, None)
        raise HTTPException(429, f"the video queue is full ({MAX_QUEUE} waiting); try again later") from None
    return _public(job_id)


@app.get("/v1/videos/{job_id}")
def status(job_id: str) -> dict:
    if job_id not in jobs:
        raise HTTPException(404, f"no video {job_id}")
    return _public(job_id)


@app.get("/v1/videos/{job_id}/content")
def content(job_id: str) -> FileResponse:
    if not JOB_ID_PATTERN.match(job_id):
        raise HTTPException(404, f"no video {job_id}")
    job = jobs.get(job_id) or {}
    path = Path(job.get("path") or (config.VIDEO_DIR / f"{job_id}.mp4"))
    resolved = path.resolve()
    if not resolved.is_relative_to(config.VIDEO_DIR.resolve()) or not resolved.exists():
        raise HTTPException(404, f"video {job_id} is not ready")
    return FileResponse(resolved, media_type="video/mp4")
