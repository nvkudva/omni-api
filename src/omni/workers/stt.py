import asyncio
import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import File, Form, UploadFile
from fastapi.responses import PlainTextResponse

from omni import config, workers

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


@app.get("/health")
def health() -> dict:
    return {"ok": True, "model": model_name, "loaded": model.loaded}


@app.post("/v1/audio/transcriptions")
async def transcriptions(
    file: Annotated[UploadFile, File()],
    model_field: Annotated[str | None, Form(alias="model")] = None,
    response_format: Annotated[str, Form()] = "json",
):
    suffix = Path(file.filename or "audio").suffix or ".wav"
    with tempfile.NamedTemporaryFile(suffix=suffix) as upload:
        upload.write(await file.read())
        upload.flush()
        result = await asyncio.to_thread(_transcribe, Path(upload.name))
    if response_format == "text":
        return PlainTextResponse(result.text)
    if response_format != "verbose_json":
        return {"text": result.text}
    segments = [
        {"id": index, "start": round(sentence.start, 2), "end": round(sentence.end, 2), "text": sentence.text.strip()}
        for index, sentence in enumerate(result.sentences)
    ]
    duration = segments[-1]["end"] if segments else 0.0
    return {"task": "transcribe", "duration": duration, "text": result.text, "segments": segments}
