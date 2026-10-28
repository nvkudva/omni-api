"""Text to speech: POST /v1/audio/speech and GET /v1/audio/voices on Kokoro, Kitten TTS or Supertonic through ONNX Runtime."""
import io
import json
import re
import wave
from pathlib import Path

import numpy as np
from fastapi import HTTPException
from fastapi.responses import Response

from omni import config, schemas, workers
from omni.registry import roles
from omni.workers import tts_engines

SAMPLE_RATE = 24000
# The model reads at most 510 phoneme tokens at once; the two pad tokens make 512.
MAX_PHONEMES = 500
STYLE_SIZE = 256

settings = config.load()
model_name = settings["tts"]["model"]
VOICES = roles.tts_voices(model_name)
# a configured voice from another family (the shipped default is a Kokoro one) falls back to this model's own
default_voice = roles.tts_default_voice(model_name, settings["tts"]["voice"])
# fail fast on a model no engine here loads instead of on the first request
roles.require("tts", model_name)


class Speaker:
    """Kokoro: the ONNX session, the phoneme vocabulary and per-accent text-to-phoneme converters."""
    sample_rate = SAMPLE_RATE

    def __init__(self, folder: Path):
        import onnxruntime

        self.folder = folder
        vocab = json.loads((folder / "tokenizer.json").read_text())["model"]["vocab"]
        self.token_ids = vocab
        self.session = onnxruntime.InferenceSession(
            str(folder / "onnx" / "model_quantized.onnx"), providers=["CPUExecutionProvider"])
        self.converters: dict[str, object] = {}
        self.styles: dict[str, np.ndarray] = {}

    def phonemes(self, text: str, accent: str) -> str:
        if accent not in self.converters:
            from misaki import en, espeak

            is_british = accent == "b"
            self.converters[accent] = en.G2P(trf=False, british=is_british,
                                             fallback=espeak.EspeakFallback(british=is_british))
        return self.converters[accent](text)[0]

    def style(self, voice: str) -> np.ndarray:
        if voice not in self.styles:
            path = self.folder / "voices" / f"{voice}.bin"
            self.styles[voice] = np.fromfile(path, dtype=np.float32).reshape(-1, STYLE_SIZE)
        return self.styles[voice]

    def synthesize(self, text: str, voice: str, speed: float) -> np.ndarray:
        pieces = [self.phonemes(sentence, voice[0]) for sentence in _sentences(text)]
        audio = [self.speak(chunk, voice, speed) for chunk in _phoneme_chunks(pieces)]
        return np.concatenate(audio) if audio else np.zeros(0, dtype=np.float32)

    def speak(self, phonemes: str, voice: str, speed: float) -> np.ndarray:
        ids = [self.token_ids[char] for char in phonemes if char in self.token_ids]
        if not ids:
            return np.zeros(0, dtype=np.float32)
        style = self.style(voice)[len(ids)][None]
        outputs = self.session.run(None, {
            "input_ids": np.array([[0, *ids, 0]], dtype=np.int64),
            "style": style,
            "speed": np.array([speed], dtype=np.float32),
        })
        return np.asarray(outputs[0], dtype=np.float32).reshape(-1)


ENGINES = {"kokoro": Speaker, "kitten": tts_engines.Kitten, "supertonic": tts_engines.Supertonic}


def _load():
    from huggingface_hub import snapshot_download

    folder = Path(snapshot_download(model_name, allow_patterns=roles.files("tts", model_name)))
    return ENGINES[roles.backend("tts", model_name)](folder)


model = workers.Lazy(_load)
executor = workers.ModelExecutor("tts-model", max_pending=8)
app = workers.app(model, model_executor=executor)


def _sentences(text: str) -> list[str]:
    return [piece for piece in re.split(r"(?<=[.!?])\s+|\n+", text) if piece.strip()]


def _phoneme_chunks(phoneme_pieces: list[str]) -> list[str]:
    """Pack sentence phonemes into runs the model accepts; one longer than the limit is cut hard."""
    chunks: list[str] = []
    current = ""
    for piece in phoneme_pieces:
        if current and len(current) + 1 + len(piece) > MAX_PHONEMES:
            chunks.append(current)
            current = ""
        while len(piece) > MAX_PHONEMES:
            chunks.append(piece[:MAX_PHONEMES])
            piece = piece[MAX_PHONEMES:]
        current = f"{current} {piece}".strip()
    if current:
        chunks.append(current)
    return chunks


def _synthesize(text: str, voice: str, speed: float) -> tuple[np.ndarray, int]:
    with model.use() as engine:
        return engine.synthesize(text, voice, speed), engine.sample_rate


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
    return {"ok": True, "model": model_name, "loaded": model.loaded, "busy": executor.pending}


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
    samples, sample_rate = await executor.run_request(_synthesize, req.input, voice, req.speed)
    if samples.size == 0:
        raise HTTPException(400, "input: nothing speakable in this text")
    pcm = _pcm16(samples)
    if req.response_format == "pcm":
        return Response(pcm, media_type="audio/pcm", headers={"x-sample-rate": str(sample_rate)})
    return Response(_wav(pcm, sample_rate), media_type="audio/wav")
