"""Speech to text: POST /v1/audio/transcriptions on Parakeet (int8 ONNX through onnx-asr), or Moonshine, Whisper and oruk through transformers."""
import asyncio
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
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


# transformers cuts audio into pieces of this length and joins their text
PIPELINE_CHUNK_SECONDS = 30
# oruk's repo ships its own modeling code, which transformers must execute: only this exact repo is trusted,
# since the family marker alone would match any repo with "oruk" in its name
TRUSTED_REMOTE_CODE = ("oruk/oruk-loop-153M",)


class PipelineTranscriber:
    """Moonshine and Whisper through the transformers speech pipeline, on the CPU."""

    def __init__(self, folder: Path):
        from transformers import pipeline

        self.pipe = pipeline("automatic-speech-recognition", model=str(folder), device="cpu")

    def recognize(self, samples: np.ndarray, sample_rate: int):
        out = self.pipe({"raw": samples, "sampling_rate": sample_rate}, chunk_length_s=PIPELINE_CHUNK_SECONDS)
        return SimpleNamespace(text=out["text"], tokens=None, timestamps=None)


class OrukTranscriber:
    """oruk-loop: the repo's own `transcribe`, which cuts long audio itself."""

    def __init__(self, folder: Path):
        from transformers import AutoModel, AutoProcessor

        self.model = AutoModel.from_pretrained(str(folder), trust_remote_code=True, device="cpu")
        self.processor = AutoProcessor.from_pretrained(str(folder))

    def recognize(self, samples: np.ndarray, sample_rate: int):
        return SimpleNamespace(text=self.model.transcribe((samples, sample_rate), self.processor), tokens=None, timestamps=None)


def _load():
    roles.require("stt", model_name)
    backend = roles.backend("stt", model_name)
    if backend == "parakeet":
        import onnx_asr

        return onnx_asr.load_model(model_name, quantization="int8").with_timestamps()
    from huggingface_hub import snapshot_download

    if backend == "oruk" and model_name not in TRUSTED_REMOTE_CODE:
        raise config.ConfigError(f"stt.model {model_name!r} would run code from its repo; only {', '.join(TRUSTED_REMOTE_CODE)} is trusted to")
    folder = Path(snapshot_download(model_name, allow_patterns=roles.files("stt", model_name)))
    return (OrukTranscriber if backend == "oruk" else PipelineTranscriber)(folder)


model = workers.Lazy(_load)
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
    text = " ".join(text for text in texts if text)
    if text and not segments:
        # the transformers models report no token times, so the whole file is one segment
        segments = [{"start": 0.0, "end": round(len(samples) / SAMPLE_RATE, 2), "text": text}]
    return text, segments


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
