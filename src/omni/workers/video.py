"""LTX-2.5 distilled text/image-to-video on MLX, as OpenAI-style jobs: create, poll, fetch the mp4."""
import asyncio
import base64
import time
import uuid
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from omni import config, workers

MAX_SIDE = 1280
MAX_SECONDS = 10

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
app = workers.app(model)
jobs: dict[str, dict] = {}
queue: asyncio.Queue[tuple[str, "VideoRequest"]] = asyncio.Queue()


class VideoRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    model: str | None = None
    size: str = Field("704x480", pattern=r"^\d{3,4}x\d{3,4}$")
    seconds: float = Field(settings["video"]["frames"] / fps, gt=0, le=MAX_SECONDS)
    seed: int = 42
    negative_prompt: str | None = None
    image: str | None = Field(None, description="base64 first frame, optional")


def _dimensions(size: str) -> tuple[int, int]:
    width, height = (int(side) for side in size.split("x"))
    if not (256 <= width <= MAX_SIDE and 256 <= height <= MAX_SIDE):
        raise HTTPException(400, f"size: each side between 256 and {MAX_SIDE}")
    # stage one runs at half size and the latents patch in 32-pixel blocks
    return width - width % 64, height - height % 64


def _frames(seconds: float) -> int:
    # the VAE packs 8 frames per latent step after the first
    steps = max(1, round(seconds * fps / 8))
    return steps * 8 + 1


def _public(job_id: str) -> dict:
    job = jobs[job_id]
    return {key: value for key, value in job.items() if key != "path"}


def _generate(job_id: str, req: VideoRequest) -> None:
    job = jobs[job_id]
    width, height = _dimensions(req.size)
    path = config.VIDEO_DIR / f"{job_id}.mp4"
    image_path = None
    if req.image:
        image_path = config.VIDEO_DIR / f"{job_id}.png"
        image_path.write_bytes(base64.b64decode(req.image.split(",")[-1]))
    with model.use() as pipe:
        job["status"] = "in_progress"
        pipe.generate_and_save(req.prompt, str(path), height=height, width=width, num_frames=_frames(req.seconds),
                               frame_rate=fps, seed=req.seed, image=str(image_path) if image_path else None,
                               negative_prompt=req.negative_prompt)
    if image_path:
        image_path.unlink(missing_ok=True)
    job.update(status="completed", completed_at=int(time.time()), path=str(path))


async def _run_jobs() -> None:
    while True:
        job_id, req = await queue.get()
        try:
            await asyncio.to_thread(_generate, job_id, req)
        except Exception as error:
            jobs[job_id].update(status="failed", error={"message": f"{type(error).__name__}: {error}"})


@app.on_event("startup")
async def start_runner() -> None:
    asyncio.create_task(_run_jobs())


@app.get("/health")
def health() -> dict:
    return {"ok": True, "model": model_name, "loaded": model.loaded, "queued": queue.qsize()}


@app.post("/v1/videos")
async def create(req: VideoRequest) -> dict:
    width, height = _dimensions(req.size)
    job_id = f"video_{uuid.uuid4().hex[:16]}"
    jobs[job_id] = {"id": job_id, "object": "video", "model": model_name, "status": "queued",
                    "created_at": int(time.time()), "size": f"{width}x{height}",
                    "seconds": round(_frames(req.seconds) / fps, 2), "prompt": req.prompt}
    await queue.put((job_id, req))
    return _public(job_id)


@app.get("/v1/videos/{job_id}")
def status(job_id: str) -> dict:
    if job_id not in jobs:
        raise HTTPException(404, f"no video {job_id}")
    return _public(job_id)


@app.get("/v1/videos/{job_id}/content")
def content(job_id: str) -> FileResponse:
    path = Path(jobs.get(job_id, {}).get("path", config.VIDEO_DIR / f"{job_id}.mp4"))
    if not path.exists():
        raise HTTPException(404, f"video {job_id} is not ready")
    return FileResponse(path, media_type="video/mp4")
