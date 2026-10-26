"""Decoding an image a request carries as base64, shared by the workers that take one."""
import base64
import binascii
import io

from fastapi import HTTPException
from PIL import Image, UnidentifiedImageError

from omni import schemas


def decode(data: str) -> bytes:
    """Validate a base64 image up front, so a bad one is a 400 and not a failed job."""
    raw = data.split(",", 1)[-1] if data.startswith("data:") else data
    try:
        blob = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as error:
        raise HTTPException(400, "image: not valid base64") from error
    if len(blob) > schemas.IMAGE_MAX_FRAME_BYTES:
        raise HTTPException(413, "image: larger than the limit")
    try:
        with Image.open(io.BytesIO(blob)) as picture:
            picture.verify()
    except (UnidentifiedImageError, OSError) as error:
        raise HTTPException(400, "image: not a decodable image") from error
    return blob
