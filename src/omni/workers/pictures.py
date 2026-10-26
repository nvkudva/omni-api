"""Decoding an image a request carries as base64, shared by the workers that take one."""
import base64
import binascii
import io

from fastapi import HTTPException
from PIL import Image, UnidentifiedImageError

from omni import schemas

# A phone photo can be 200 MP, and a solid-colour PNG reaches PIL's 179M-pixel bomb limit in about
# a megabyte. Both are refused here, before a worker converts them and eats hundreds of MB.
MAX_IMAGE_SIDE = 8000


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
            if max(picture.size) > MAX_IMAGE_SIDE:
                raise HTTPException(413, f"image: larger than {MAX_IMAGE_SIDE} px on a side")
            picture.verify()
    except Image.DecompressionBombError as error:
        raise HTTPException(413, "image: too many pixels") from error
    except (UnidentifiedImageError, OSError) as error:
        raise HTTPException(400, "image: not a decodable image") from error
    return blob
