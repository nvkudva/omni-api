"""Request models and size parsing: the bounds the gateway and workers rely on."""
import unittest

from pydantic import ValidationError

from omni import schemas


class ParseSize(unittest.TestCase):
    def test_aligns_down_to_the_step(self):
        self.assertEqual(schemas.parse_size("1024x1024", max_side=2048, align=16), (1024, 1024))
        # 480 % 64 == 32, so the height drops to 448
        self.assertEqual(schemas.parse_size("704x480", max_side=2048, align=64), (704, 448))

    def test_rejects_sides_out_of_range(self):
        with self.assertRaises(ValueError):
            schemas.parse_size("100x100", max_side=2048, align=16)  # below MIN_SIDE
        with self.assertRaises(ValueError):
            schemas.parse_size("4096x4096", max_side=2048, align=16)

    def test_rejects_non_numeric(self):
        with self.assertRaises(ValueError):
            schemas.parse_size("axb", max_side=2048, align=16)


class ModelMatches(unittest.TestCase):
    def test_missing_empty_configured_or_role_all_match(self):
        self.assertTrue(schemas.model_matches(None, "m", "image"))
        self.assertTrue(schemas.model_matches("", "m", "image"))
        self.assertTrue(schemas.model_matches("m", "m", "image"))
        self.assertTrue(schemas.model_matches("image", "m", "image"))

    def test_a_different_name_does_not(self):
        self.assertFalse(schemas.model_matches("other", "m", "image"))


class RequestBounds(unittest.TestCase):
    def test_image_request(self):
        schemas.ImageRequest(prompt="a cat")
        for bad in ({"prompt": ""}, {"prompt": "x", "n": 0}, {"prompt": "x", "n": 9},
                    {"prompt": "x", "size": "10x10"}, {"prompt": "x", "steps": 0},
                    {"prompt": "x", "steps": 1000}, {"prompt": "x", "response_format": "url"}):
            with self.assertRaises(ValidationError):
                schemas.ImageRequest(**bad)

    def test_video_request(self):
        schemas.VideoRequest(prompt="a dog")
        for bad in ({"prompt": "x", "seconds": 0}, {"prompt": "x", "seconds": 11},
                    {"prompt": "x", "size": "1x1"}):
            with self.assertRaises(ValidationError):
                schemas.VideoRequest(**bad)

    def test_speech_request(self):
        schemas.SpeechRequest(input="hello")
        for bad in ({"input": ""}, {"input": "x", "speed": 0.1}, {"input": "x", "speed": 5.0},
                    {"input": "x", "response_format": "mp3"}):
            with self.assertRaises(ValidationError):
                schemas.SpeechRequest(**bad)

    def test_prompt_length_cap(self):
        with self.assertRaises(ValidationError):
            schemas.ImageRequest(prompt="x" * (schemas.MAX_PROMPT_CHARS + 1))


if __name__ == "__main__":
    unittest.main()
