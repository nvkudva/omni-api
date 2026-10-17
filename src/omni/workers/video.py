"""LTX-2.5 distilled text/image-to-video on MLX, as OpenAI-style jobs: create, poll, fetch the mp4.

Jobs are capped, expire on a TTL and their mp4s are deleted, so a long session cannot grow without bound.
"""
import asyncio
import base64
import binascii
import io
import logging
import re
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from PIL import Image, UnidentifiedImageError

from omni import config, schemas, workers
from omni.registry import pulling

log = logging.getLogger("omni.worker.video")

MAX_QUEUE = 8
JOB_TTL_SECONDS = 3600
SWEEP_SECONDS = 60
JOB_ID_PATTERN = re.compile(r"^video_[0-9a-f]{16}$")

settings = config.load()
model_name = settings["video"]["model"]
fps = settings["video"]["fps"]
config.ensure_dirs()


def _load():
    from ltx_pipelines_mlx import DistilledPipeline

    return DistilledPipeline(model_dir=model_name, low_memory=True)


def _clear_cache() -> None:
    import mlx.core

    mlx.core.clear_cache()


model = workers.Lazy(_load, config.idle_seconds(settings), _clear_cache)
executor = workers.ModelExecutor("video-model")
jobs: dict[str, dict] = {}
queue: asyncio.Queue[tuple[str, schemas.VideoRequest, int, int]] = asyncio.Queue(maxsize=MAX_QUEUE)


def _frames(seconds: float) -> int:
    # the VAE packs 8 frames per latent step after the first
    return max(1, round(seconds * fps / 8)) * 8 + 1


def _decode_frame(data: str) -> bytes:
    """Validate a base64 first frame up front, so a bad one is a 400 and not a failed job."""
    raw = data.split(",", 1)[-1] if data.startswith("data:") else data
    try:
        blob = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as error:
        raise HTTPException(400, "image: not valid base64") from error
    if len(blob) > schemas.IMAGE_MAX_FRAME_BYTES:
        raise HTTPException(413, "image: first frame is too large")
    try:
        with Image.open(io.BytesIO(blob)) as picture:
            picture.verify()
    except (UnidentifiedImageError, OSError) as error:
        raise HTTPException(400, "image: first frame is not a decodable image") from error
    return blob


def _public(job_id: str) -> dict:
    return {key: value for key, value in jobs[job_id].items() if key != "path"}


def _generate(job_id: str, req: schemas.VideoRequest, width: int, height: int) -> None:
    job = jobs.get(job_id)
    if job is None:
        return
    path = config.VIDEO_DIR / f"{job_id}.mp4"
    image_path = config.VIDEO_DIR / f"{job_id}.png" if req.image else None
    try:
        if image_path is not None:
            image_path.write_bytes(_decode_frame(req.image))
        with model.use() as pipe:
            job["status"] = "in_progress"
            pipe.generate_and_save(req.prompt, str(path), height=height, width=width,
                                   num_frames=_frames(req.seconds), frame_rate=fps, seed=req.seed,
                                   image=str(image_path) if image_path else None,
                                   negative_prompt=req.negative_prompt)
        job.update(status="completed", completed_at=int(time.time()), path=str(path))
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


async def _sweep_jobs() -> None:
    """Forget finished jobs after a while and delete their mp4s."""
    while True:
        await asyncio.sleep(SWEEP_SECONDS)
        cutoff = time.time() - JOB_TTL_SECONDS
        for job_id, job in list(jobs.items()):
            if job["status"] not in ("completed", "failed"):
                continue
            finished = job.get("completed_at") or job.get("failed_at") or job.get("created_at")
            if finished and finished < cutoff:
                if job.get("path"):
                    Path(job["path"]).unlink(missing_ok=True)
                jobs.pop(job_id, None)


@asynccontextmanager
async def lifespan(_: FastAPI):
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
    return {"ok": True, "model": model_name, "loaded": model.loaded, "queued": queue.qsize()}


@app.post("/v1/videos")
async def create(req: schemas.VideoRequest) -> dict:
    if not schemas.model_matches(req.model, model_name, "video"):
        raise HTTPException(400, f"model: this worker serves only {model_name}")
    try:
        width, height = schemas.parse_size(req.size, max_side=schemas.VIDEO_MAX_SIDE, align=schemas.VIDEO_ALIGN)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    if req.image:
        _decode_frame(req.image)
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
