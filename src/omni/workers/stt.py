"""Speech to text: POST /v1/audio/transcriptions on parakeet-mlx."""
import asyncio
import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import File, Form, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse

from omni import config, schemas, workers

settings = config.load()
model_name = settings["stt"]["model"]


def _load():
    import parakeet_mlx

    return parakeet_mlx.from_pretrained(model_name)


model = workers.Lazy(_load, config.idle_seconds(settings))
app = workers.app(model)


def _transcribe(path: Path):
    # parakeet decodes the file with ffmpeg, so any container the browser records (webm, ogg, m4a) works.
    with model.use() as transcriber:
        return transcriber.transcribe(path)


def _suffix(filename: str | None) -> str:
    """A safe, short extension for the temp file ffmpeg will read."""
    suffix = Path(filename or "audio").suffix.lower()
    if suffix and len(suffix) <= 8 and suffix[1:].isalnum() and suffix.isascii():
        return suffix
    return ".wav"


@app.get("/health")
def health() -> dict:
    return {"ok": True, "model": model_name, "loaded": model.loaded}


@app.post("/v1/audio/transcriptions")
async def transcriptions(
    file: Annotated[UploadFile, File()],
    model_field: Annotated[str | None, Form(alias="model")] = None,
    response_format: Annotated[str, Form()] = "json",
):
    if not schemas.model_matches(model_field, model_name, "stt"):
        raise HTTPException(400, f"model: this worker serves only {model_name}")
    if response_format not in schemas.STT_FORMATS:
        raise HTTPException(400, f"response_format: one of {', '.join(schemas.STT_FORMATS)}")

    limit_mb = schemas.STT_MAX_BYTES // 1024 // 1024
    data = await file.read(schemas.STT_MAX_BYTES + 1)
    if not data:
        raise HTTPException(400, "file: empty")
    if len(data) > schemas.STT_MAX_BYTES:
        raise HTTPException(413, f"file: larger than {limit_mb} MB")

    with tempfile.NamedTemporaryFile(suffix=_suffix(file.filename)) as upload:
        upload.write(data)
        upload.flush()
        result = await asyncio.to_thread(_transcribe, Path(upload.name))

    if response_format == "text":
        return PlainTextResponse(result.text)
    if response_format != "verbose_json":
        return {"text": result.text}
    segments = [
        {"id": index, "start": round(sentence.start, 2), "end": round(sentence.end, 2),
         "text": sentence.text.strip()}
        for index, sentence in enumerate(result.sentences)
    ]
    duration = segments[-1]["end"] if segments else 0.0
    return {"task": "transcribe", "duration": duration, "text": result.text, "segments": segments}
