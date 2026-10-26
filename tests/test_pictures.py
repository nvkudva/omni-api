"""What a base64 image may be before a worker ever decodes it."""
import base64
import io
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from PIL import Image

from omni.workers import pictures


def data_url(picture) -> str:
    buffer = io.BytesIO()
    picture.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


class Decode(unittest.TestCase):
    def test_a_normal_image_passes_through(self):
        self.assertTrue(pictures.decode(data_url(Image.new("RGB", (32, 32)))))

    def test_too_many_pixels_is_a_413(self):
        with patch.object(Image, "MAX_IMAGE_PIXELS", 100), self.assertRaises(HTTPException) as caught:
            pictures.decode(data_url(Image.new("RGB", (200, 200))))
        self.assertEqual(caught.exception.status_code, 413)

    def test_a_side_over_the_cap_is_a_413(self):
        with self.assertRaises(HTTPException) as caught:
            pictures.decode(data_url(Image.new("1", (pictures.MAX_IMAGE_SIDE + 1, 1))))
        self.assertEqual(caught.exception.status_code, 413)
