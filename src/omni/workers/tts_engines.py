"""The text-to-speech engines besides Kokoro: Kitten TTS (one ONNX file plus a phonemizer) and Supertonic (its own SDK)."""
import os
import re
from pathlib import Path

import numpy as np

# KittenTTS trims this many trailing samples: the model ends each chunk with a click.
KITTEN_TAIL_SAMPLES = 5000
KITTEN_SAMPLE_RATE = 24000
# Kitten reads a bounded phoneme sequence per call, so text goes through in sentence-sized pieces.
KITTEN_CHUNK_CHARS = 400
KITTEN_PAD = "$"
KITTEN_PUNCTUATION = ';:,.!?¡¿—…"«»"" '
KITTEN_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
KITTEN_LETTERS_IPA = (
    "ɑɐɒæɓʙβɔɕçɗɖðʤəɘɚɛɜɝɞɟʄɡɠɢʛɦɧħɥʜɨɪʝɭɬɫɮʟɱɯɰŋɳɲɴøɵɸθœɶʘɹɺɾɻʀʁɽʂʃʈʧʉʊʋⱱʌɣɤʍχʎʏʑʐʒʔʡʕʢǀǁǂǃˈˌːˑʼʴʰʱʲʷˠˤ˞↓↑→↗↘'̩'ᵻ"
)
KITTEN_SYMBOLS = {symbol: index for index, symbol in
                  enumerate([KITTEN_PAD, *KITTEN_PUNCTUATION, *KITTEN_LETTERS, *KITTEN_LETTERS_IPA])}
KITTEN_END_TOKEN = 10
SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+|\n+")


def _pieces(text: str, limit: int) -> list[str]:
    """Sentences packed up to `limit` characters; one sentence longer than that is cut at the limit."""
    pieces: list[str] = []
    current = ""
    for sentence in (part.strip() for part in SENTENCE_BREAK.split(text)):
        if not sentence:
            continue
        while len(sentence) > limit:
            if current:
                pieces.append(current)
                current = ""
            pieces.append(sentence[:limit])
            sentence = sentence[limit:]
        if current and len(current) + 1 + len(sentence) > limit:
            pieces.append(current)
            current = ""
        current = f"{current} {sentence}".strip()
    if current:
        pieces.append(current)
    return pieces


class Kitten:
    """Kitten TTS 0.8: StyleTTS-style ONNX model; espeak phonemes in, 24 kHz audio out."""
    sample_rate = KITTEN_SAMPLE_RATE

    def __init__(self, folder: Path):
        import json

        import espeakng_loader
        import onnxruntime
        import phonemizer
        from phonemizer.backend.espeak.wrapper import EspeakWrapper

        EspeakWrapper.set_library(espeakng_loader.get_library_path())
        os.environ["ESPEAK_DATA_PATH"] = espeakng_loader.get_data_path()
        config = json.loads((folder / "config.json").read_text())
        self.aliases = config.get("voice_aliases", {})
        self.speed_priors = config.get("speed_priors", {})
        self.voices = np.load(folder / "voices.npz")
        self.session = onnxruntime.InferenceSession(
            str(folder / config["model_file"]), providers=["CPUExecutionProvider"])
        self.phonemizer = phonemizer.backend.EspeakBackend(
            language="en-us", preserve_punctuation=True, with_stress=True)

    def _tokens(self, text: str) -> list[int]:
        phonemes = self.phonemizer.phonemize([text])[0]
        spaced = " ".join(re.findall(r"\w+|[^\w\s]", phonemes))
        tokens = [KITTEN_SYMBOLS[char] for char in spaced if char in KITTEN_SYMBOLS]
        return [0, *tokens, KITTEN_END_TOKEN, 0]

    def _speak(self, text: str, voice: str, speed: float) -> np.ndarray:
        name = self.aliases.get(voice, voice)
        styles = self.voices[name]
        row = min(len(text), styles.shape[0] - 1)
        style = styles[row:row + 1]
        outputs = self.session.run(None, {
            "input_ids": np.array([self._tokens(text)], dtype=np.int64),
            "style": style,
            "speed": np.array([speed * self.speed_priors.get(name, 1.0)], dtype=np.float32),
        })
        return np.asarray(outputs[0], dtype=np.float32).reshape(-1)[:-KITTEN_TAIL_SAMPLES]

    def synthesize(self, text: str, voice: str, speed: float) -> np.ndarray:
        audio = [self._speak(piece, voice, speed) for piece in _pieces(text, KITTEN_CHUNK_CHARS)]
        return np.concatenate(audio) if audio else np.zeros(0, dtype=np.float32)


class Supertonic:
    """Supertonic 3 through its own SDK, pointed at the downloaded repo so nothing is fetched at load time."""

    def __init__(self, folder: Path):
        from supertonic import TTS

        self.engine = TTS(model="supertonic-3", model_dir=folder, auto_download=False)
        self.sample_rate = self.engine.sample_rate
        self.styles: dict[str, object] = {}

    def synthesize(self, text: str, voice: str, speed: float) -> np.ndarray:
        if voice not in self.styles:
            self.styles[voice] = self.engine.get_voice_style(voice)
        waveform, _ = self.engine.synthesize(text, voice_style=self.styles[voice], speed=speed)
        return np.asarray(waveform, dtype=np.float32).reshape(-1)
