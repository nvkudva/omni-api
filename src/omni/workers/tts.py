"""Text to speech: POST /v1/audio/speech and GET /v1/audio/voices on Kokoro through mlx-audio."""
import asyncio
import io
import wave

import numpy as np
from fastapi import HTTPException
from fastapi.responses import Response

from omni import config, schemas, workers

VOICES = schemas.TTS_VOICES

settings = config.load()
model_name = settings["tts"]["model"]
default_voice = settings["tts"]["voice"]
# fail fast on a bad shipped default instead of on the first request
if default_voice not in VOICES:
    raise config.ConfigError(f"tts.voice {default_voice!r} is not a known Kokoro voice")


def _load():
    from mlx_audio.tts.utils import load_model

    return load_model(model_name)


model = workers.Lazy(_load, config.idle_seconds(settings))
app = workers.app(model)


def _synthesize(text: str, voice: str, speed: float) -> tuple[np.ndarray, int]:
    chunks = []
    sample_rate = 24000
    with model.use() as speaker:
        for segment in speaker.generate(text=text, voice=voice, speed=speed, lang_code=voice[0]):
            chunks.append(np.asarray(segment.audio, dtype=np.float32).reshape(-1))
            sample_rate = getattr(segment, "sample_rate", sample_rate)
    return (np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)), sample_rate


def _pcm16(samples: np.ndarray) -> bytes:
    return (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2").tobytes()


def _wav(pcm: bytes, sample_rate: int) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(sample_rate)
        writer.writeframes(pcm)
    return buffer.getvalue()


@app.get("/health")
def health() -> dict:
    return {"ok": True, "model": model_name, "loaded": model.loaded}


@app.get("/v1/audio/voices")
def voices() -> dict:
    return {"default": default_voice, "voices": list(VOICES)}


@app.post("/v1/audio/speech")
async def speech(req: schemas.SpeechRequest) -> Response:
    if not schemas.model_matches(req.model, model_name, "tts"):
        raise HTTPException(400, f"model: this worker serves only {model_name}")
    voice = req.voice or default_voice
    if voice not in VOICES:
        raise HTTPException(422, f"unknown voice {voice!r}; GET /v1/audio/voices lists them")
    samples, sample_rate = await asyncio.to_thread(_synthesize, req.input, voice, req.speed)
    if samples.size == 0:
        raise HTTPException(500, "synthesis produced no audio for this input")
    pcm = _pcm16(samples)
    if req.response_format == "pcm":
        return Response(pcm, media_type="audio/pcm", headers={"x-sample-rate": str(sample_rate)})
    return Response(_wav(pcm, sample_rate), media_type="audio/wav")
