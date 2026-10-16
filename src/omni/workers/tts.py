import asyncio
import io
import wave
from typing import Literal

import numpy as np
from fastapi import HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from omni import config, workers

# Kokoro's American (a) and British (b) English voices; misaki[en] covers only these two.
VOICES = [
    "af_heart", "af_alloy", "af_aoede", "af_bella", "af_jessica", "af_kore", "af_nicole", "af_nova",
    "af_river", "af_sarah", "af_sky", "am_adam", "am_echo", "am_eric", "am_fenrir", "am_liam",
    "am_michael", "am_onyx", "am_puck", "bf_alice", "bf_emma", "bf_isabella", "bf_lily",
    "bm_daniel", "bm_fable", "bm_george", "bm_lewis",
]

settings = config.load()
model_name = settings["tts"]["model"]
default_voice = settings["tts"]["voice"]


def _load():
    from mlx_audio.tts.utils import load_model

    return load_model(model_name)


model = workers.Lazy(_load, config.idle_seconds(settings))
app = workers.app(model)


class SpeechRequest(BaseModel):
    input: str = Field(min_length=1, max_length=4096)
    model: str | None = None
    voice: str | None = None
    speed: float = Field(1.0, ge=0.5, le=2.0)
    response_format: Literal["wav", "pcm"] = "wav"


def _synthesize(text: str, voice: str, speed: float) -> tuple[np.ndarray, int]:
    chunks = []
    sample_rate = 24000
    with model.use() as speaker:
        for segment in speaker.generate(text=text, voice=voice, speed=speed, lang_code=voice[0]):
            chunks.append(np.asarray(segment.audio, dtype=np.float32).reshape(-1))
            sample_rate = getattr(segment, "sample_rate", sample_rate)
    return np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32), sample_rate


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
    return {"default": default_voice, "voices": VOICES}


@app.post("/v1/audio/speech")
async def speech(req: SpeechRequest) -> Response:
    voice = req.voice or default_voice
    if voice not in VOICES:
        raise HTTPException(422, f"unknown voice {voice!r}; GET /v1/audio/voices lists them")
    samples, sample_rate = await asyncio.to_thread(_synthesize, req.input, voice, req.speed)
    pcm = _pcm16(samples)
    if req.response_format == "pcm":
        return Response(pcm, media_type="audio/pcm", headers={"x-sample-rate": str(sample_rate)})
    return Response(_wav(pcm, sample_rate), media_type="audio/wav")
