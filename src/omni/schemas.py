"""Request models and numeric bounds shared by the media and speech workers.

Keeping the limits here means the gateway, the workers and the playground can agree on them, and a
request that is too big or out of range is rejected before a model is ever touched.
"""
from pydantic import BaseModel, Field

# size strings are "WxH"; every round worker only accepts sane, even dimensions
SIZE_PATTERN = r"^\d{3,4}x\d{3,4}$"
MIN_SIDE = 256

IMAGE_MAX_SIDE = 2048
IMAGE_ALIGN = 16
IMAGE_MAX_STEPS = 100
IMAGE_MAX_IMAGES = 4
IMAGE_MAX_FRAME_BYTES = 25 * 1024 * 1024

VIDEO_MAX_SIDE = 1280
VIDEO_ALIGN = 64
VIDEO_MAX_SECONDS = 10.0
VIDEO_DEFAULT_SECONDS = 4.0
VIDEO_DEFAULT_SEED = 42

MAX_PROMPT_CHARS = 4000
MAX_NEGATIVE_CHARS = 1000

EMBED_INPUT_TYPES = ("query", "document")
EMBED_MAX_INPUTS = 256
EMBED_MAX_CHARS = 2000

STT_FORMATS = ("json", "text", "verbose_json")
STT_MAX_BYTES = 100 * 1024 * 1024
# What each speech runtime loads from its Hugging Face repo, so a download skips the other quants.
STT_FILES = ["config.json", "vocab.txt", "encoder-model.int8.onnx", "decoder_joint-model.int8.onnx"]
TTS_FILES = ["config.json", "tokenizer.json", "onnx/model_quantized.onnx", "voices/*.bin"]
DECIDE_FILES = ["v2/embed_q8.onnx*", "v2/decoder_q8.onnx*", "v2/engine.onnx", "v2/tokenizer.json", "v2/vega_web.json",
                "v2/vision_q8.onnx*"]

# Kokoro's American (a) and British (b) English voices; misaki[en] covers only these two.
TTS_VOICES = (
    "af_heart", "af_alloy", "af_aoede", "af_bella", "af_jessica", "af_kore", "af_nicole", "af_nova",
    "af_river", "af_sarah", "af_sky", "am_adam", "am_echo", "am_eric", "am_fenrir", "am_liam",
    "am_michael", "am_onyx", "am_puck", "bf_alice", "bf_emma", "bf_isabella", "bf_lily",
    "bm_daniel", "bm_fable", "bm_george", "bm_lewis",
)
TTS_MIN_SPEED = 0.5
TTS_MAX_SPEED = 2.0
TTS_MAX_CHARS = 4096


class ImageRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=MAX_PROMPT_CHARS)
    model: str | None = None
    n: int = Field(1, ge=1, le=IMAGE_MAX_IMAGES)
    size: str = Field("1024x1024", pattern=SIZE_PATTERN)
    negative_prompt: str | None = Field(None, max_length=MAX_NEGATIVE_CHARS)
    steps: int | None = Field(None, ge=1, le=IMAGE_MAX_STEPS)
    seed: int | None = Field(None, ge=0, le=2**63 - 1)
    response_format: str = Field("b64_json", pattern="^b64_json$")


class VideoRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=MAX_PROMPT_CHARS)
    model: str | None = None
    size: str = Field("704x480", pattern=SIZE_PATTERN)
    seconds: float = Field(VIDEO_DEFAULT_SECONDS, gt=0, le=VIDEO_MAX_SECONDS)
    seed: int = Field(VIDEO_DEFAULT_SEED, ge=0, le=2**63 - 1)
    negative_prompt: str | None = Field(None, max_length=MAX_NEGATIVE_CHARS)
    image: str | None = Field(None, description="base64 first frame, optional")


class SpeechRequest(BaseModel):
    input: str = Field(min_length=1, max_length=TTS_MAX_CHARS)
    model: str | None = None
    voice: str | None = None
    speed: float = Field(1.0, ge=TTS_MIN_SPEED, le=TTS_MAX_SPEED)
    response_format: str = Field("wav", pattern="^(wav|pcm)$")


def parse_size(size: str, *, max_side: int, align: int) -> tuple[int, int]:
    """``WxH`` -> aligned ``(width, height)``; raises ValueError when a side is out of range."""
    width, height = (int(side) for side in size.split("x"))
    for side in (width, height):
        if not MIN_SIDE <= side <= max_side:
            raise ValueError(f"size: each side must be between {MIN_SIDE} and {max_side}")
    # pipelines patch latents in blocks, so dimensions must be multiples of `align`
    return width - width % align, height - height % align


def model_matches(requested: str | None, configured: str, role: str) -> bool:
    """A worker serves one configured model; a request may name that model, the role, or nothing."""
    return requested in (None, "", configured, role)
