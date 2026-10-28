"""The speech roles serve several model families: each repo name picks its files, voices and engine."""
import unittest
from unittest.mock import patch

import numpy as np

from omni.registry import roles
from omni.workers import stt, tts_engines


class Families(unittest.TestCase):
    def test_each_repo_name_picks_its_backend_and_files(self):
        self.assertEqual(roles.backend("stt", "UsefulSensors/moonshine-tiny"), "moonshine")
        self.assertEqual(roles.backend("stt", "openai/whisper-small"), "whisper")
        self.assertEqual(roles.backend("tts", "KittenML/kitten-tts-mini-0.8"), "kitten")
        self.assertEqual(roles.files("tts", "Supertone/supertonic-3"), ["onnx/*.onnx", "onnx/*.json", "voice_styles/*.json"])
        self.assertIsNone(roles.files("tts", "someone/unknown"))

    def test_a_voice_from_another_family_falls_back_to_the_models_own(self):
        self.assertEqual(roles.tts_default_voice("onnx-community/Kokoro-82M-v1.0-ONNX", "af_heart"), "af_heart")
        self.assertEqual(roles.tts_default_voice("KittenML/kitten-tts-mini-0.8", "af_heart"), "Jasper")
        self.assertEqual(roles.tts_default_voice("Supertone/supertonic-3", "F2"), "F2")
        self.assertIn("M1", roles.tts_voices("Supertone/supertonic-3"))


class KittenText(unittest.TestCase):
    def test_sentences_pack_up_to_the_limit_and_a_long_one_is_cut(self):
        pieces = tts_engines._pieces("One. Two. " + "x" * 25, 12)
        self.assertEqual(pieces, ["One. Two.", "x" * 12, "x" * 12, "x"])

    def test_only_known_symbols_become_tokens(self):
        self.assertEqual(tts_engines.KITTEN_SYMBOLS["$"], 0)
        self.assertNotIn("\u4e2d", tts_engines.KITTEN_SYMBOLS)


class TrustedCode(unittest.TestCase):
    def test_a_repo_that_merely_has_oruk_in_its_name_is_not_trusted_to_run_code(self):
        with patch.object(stt, "model_name", "evil/oruk-fake"), patch.object(stt.roles, "require"):
            with self.assertRaises(stt.config.ConfigError):
                stt._load()


class WholeFileSegment(unittest.TestCase):
    def test_a_model_without_token_times_reports_one_segment_for_the_file(self):
        class Plain:
            def recognize(self, samples, sample_rate):
                return type("Result", (), {"text": "hello", "tokens": None, "timestamps": None})()

        samples = np.zeros(stt.SAMPLE_RATE * 2, dtype=np.float32)
        with patch.object(stt, "_decode", return_value=samples), patch.object(stt.model, "use") as use:
            use.return_value.__enter__.return_value = Plain()
            text, segments = stt._transcribe("x.wav")
        self.assertEqual((text, segments), ("hello", [{"start": 0.0, "end": 2.0, "text": "hello"}]))


if __name__ == "__main__":
    unittest.main()
