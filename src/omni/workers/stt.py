"""Speech to text: POST /v1/audio/transcriptions on Parakeet through onnx-asr (int8 ONNX)."""
import asyncio
import subprocess
import tempfile
from pathlib import Path
from typing import Annotated

import numpy as np
from fastapi import File, Form, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse

from omni import config, schemas, workers
from omni.registry import roles

SAMPLE_RATE = 16000
# Attention grows with the square of the audio length, so a long file goes through in windows.
# shortcut: a window edge can split a word, when that matters overlap the windows or cut at silence.
WINDOW_SECONDS = 180
SENTENCE_END = (".", "?", "!")

settings = config.load()
model_name = settings["stt"]["model"]


def _load():
    import onnx_asr

    roles.require("stt", model_name)
    return onnx_asr.load_model(model_name, quantization="int8").with_timestamps()


model = workers.Lazy(_load, config.idle_seconds(settings))
executor = workers.ModelExecutor("stt-model", max_pending=4)
app = workers.app(model, model_executor=executor)


def _decode(path: Path) -> np.ndarray:
    """16 kHz mono samples. ffmpeg reads any container the browser records (webm, ogg, m4a)."""
    command = ["ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-f", "f32le", "-ac", "1", "-ar", str(SAMPLE_RATE), "-"]
    try:
        done = subprocess.run(command, capture_output=True, check=False)
    except FileNotFoundError as error:
        raise HTTPException(500, "ffmpeg is not installed; stt needs it to read audio files") from error
    if done.returncode != 0:
        raise HTTPException(400, f"file: could not decode audio ({done.stderr.decode(errors='replace').strip()[-200:]})")
    return np.frombuffer(done.stdout, dtype=np.float32)


def _segments(tokens: list[str], timestamps: list[float], offset: float) -> list[dict]:
    """Sentence segments from token times: a token ending in . ? or ! closes one."""
    segments = []
    pieces: list[str] = []
    start = 0.0
    for token, time in zip(tokens, timestamps, strict=False):
        if not pieces:
            start = time
        pieces.append(token)
        if token.endswith(SENTENCE_END):
            segments.append({"start": round(offset + start, 2), "end": round(offset + time, 2),
                             "text": "".join(pieces).replace("\u2581", " ").strip()})
            pieces = []
    if pieces:
        segments.append({"start": round(offset + start, 2), "end": round(offset + timestamps[-1], 2),
                         "text": "".join(pieces).replace("\u2581", " ").strip()})
    return segments


def _transcribe(path: Path) -> tuple[str, list[dict]]:
    samples = _decode(path)
    window = WINDOW_SECONDS * SAMPLE_RATE
    texts: list[str] = []
    segments: list[dict] = []
    with model.use() as transcriber:
        for begin in range(0, len(samples), window):
            result = transcriber.recognize(samples[begin:begin + window], sample_rate=SAMPLE_RATE)
            texts.append(result.text.strip())
            segments += _segments(result.tokens or [], result.timestamps or [], begin / SAMPLE_RATE)
    return " ".join(text for text in texts if text), segments


def _write_upload(upload, data: bytes) -> None:
    upload.write(data)
    upload.flush()


def _suffix(filename: str | None) -> str:
    """A safe, short extension for the temp file ffmpeg will read."""
    suffix = Path(filename or "audio").suffix.lower()
    if suffix and len(suffix) <= 8 and suffix[1:].isalnum() and suffix.isascii():
        return suffix
    return ".wav"


@app.get("/health")
def health() -> dict:
    return {"ok": True, "model": model_name, "loaded": model.loaded, "busy": executor.pending}


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
        await asyncio.to_thread(_write_upload, upload, data)
        text, segments = await executor.run_request(_transcribe, Path(upload.name))

    if response_format == "text":
        return PlainTextResponse(text)
    if response_format != "verbose_json":
        return {"text": text}
    numbered = [{"id": index, **segment} for index, segment in enumerate(segments)]
    duration = numbered[-1]["end"] if numbered else 0.0
    return {"task": "transcribe", "duration": duration, "text": text, "segments": numbered}
