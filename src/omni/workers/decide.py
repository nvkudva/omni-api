"""Typed decisions: POST /v1/systemone on Vega (a frozen Qwen3.5-0.8B plus a physics engine) through ONNX Runtime.

The state is text, or an image (`image`: base64) that takes the text's place.
"""
import asyncio
import io
import json
import os
import shutil
from pathlib import Path

import numpy as np
from fastapi import HTTPException

from omni import config, schemas, workers
from omni.inference import vega
from omni.registry import roles
from omni.workers import pictures

MAX_QUESTIONS = 32
MAX_STATE_CHARS = 20000
# the repo holds two releases side by side; v2 is the one with the split embed and decoder graphs
RELEASE = "v2"

settings = config.load()
model_name = settings["decide"]["model"]


def _real_files(folder: Path) -> Path:
    """The release's files as real files in omni's own folder.

    ONNX Runtime refuses external weights outside the model's directory, and the Hugging Face cache
    is symlinks into `blobs/`, which resolve to different directories. A hard link costs no disk;
    a copy is the fallback when the cache is on another volume.
    """
    target = config.HOME / "decide" / folder.parent.name
    target.mkdir(parents=True, exist_ok=True)
    for source in folder.iterdir():
        destination = target / source.name
        if destination.exists():
            continue
        try:
            os.link(source.resolve(), destination)
        except OSError:
            shutil.copy2(source.resolve(), destination)
    return target


class Decider:
    """The three ONNX sessions, the tokenizer and Vega's configuration."""

    def __init__(self, folder: Path):
        import onnxruntime
        from tokenizers import Tokenizer

        self.config = json.loads((folder / "vega_web.json").read_text())
        self.tokenizer = Tokenizer.from_file(str(folder / "tokenizer.json"))
        providers = ["CPUExecutionProvider"]
        self.embed = onnxruntime.InferenceSession(str(folder / "embed_q8.onnx"), providers=providers)
        self.decoder = onnxruntime.InferenceSession(str(folder / "decoder_q8.onnx"), providers=providers)
        self.engine = onnxruntime.InferenceSession(str(folder / "engine.onnx"), providers=providers)
        self.empty_past = self._empty_past()
        self.folder = folder
        self.pad_id = self.tokenizer.token_to_id("<|image_pad|>")
        self.vision = None

    def _empty_past(self) -> dict[str, np.ndarray]:
        """Zero recurrent, conv and KV state for every past input the cut decoder keeps: the first read of a sequence."""
        feeds = {}
        for entry in self.decoder.get_inputs():
            if entry.name.startswith("past_"):
                shape = [dim if isinstance(dim, int) else (1 if dim == "batch_size" else 0) for dim in entry.shape]
                feeds[entry.name] = np.zeros(shape, dtype=np.float32)
        return feeds

    def encode(self, text: str) -> list[int]:
        return self.tokenizer.encode(text, add_special_tokens=False).ids

    def see(self, picture) -> dict:
        """The vision encoder's features for an image, computed once and shared by every question."""
        import onnxruntime

        if self.vision is None:
            self.vision = onnxruntime.InferenceSession(str(self.folder / "vision_q8.onnx"), providers=["CPUExecutionProvider"])
        patches = vega.preprocess_image(picture, self.config)
        features = self.vision.run(["image_features"], {
            "pixel_values": patches["pixels"].astype(np.float32),
            "image_grid_thw": np.array([patches["grid"]], dtype=np.int64),
        })[0]
        return {"features": features, "grid": patches["grid"], "tokens": patches["tokens"]}

    def decide(self, state_text: str, rendered: dict, image: dict | None = None) -> tuple[dict, int]:
        """The answer to one question, and the prompt's token count."""
        prompt = vega.build_prompt(self.encode, state_text, rendered, self.config)
        if image is not None:
            prompt = vega.expand_image(prompt, self.pad_id, image["tokens"])
        length = len(prompt["ids"])
        embeddings = self.embed.run(None, {"input_ids": np.array([prompt["ids"]], dtype=np.int64)})[0]
        if image is not None:
            # the image features replace the pad tokens' embeddings, in order
            embeddings[0, np.array(prompt["ids"]) == self.pad_id] = image["features"]
        layer13, layer19 = self.decoder.run(["h13", "h19"], {
            **self.empty_past,
            "inputs_embeds": embeddings,
            "attention_mask": np.ones((1, length), dtype=np.int64),
            "position_ids": vega.rope_index(prompt["ids"], self.pad_id, image["grid"] if image else None,
                                            self.config["vision_merge_size"])[:, None, :],
        })
        pooled = vega.pool(self.config, layer13[0], layer19[0], prompt, with_chunks=image is None)
        option_count = len(prompt["options"])
        chunk_count = self.config["memory_chunks"]
        logits, kinetic, nearest, steps, gates = self.engine.run(
            ["logits", "kinetic", "nearest", "steps_used", "gates"], {
                "feats": pooled["features"][None].astype(np.float32),
                "opts": pooled["options"][None, :, None].astype(np.float32),
                "opt_mask": np.ones((1, option_count), dtype=bool),
                "qtype": np.array([vega.QUESTION_TYPES[rendered["kind"]]], dtype=np.int64),
                "chunks": pooled["chunks"][None].astype(np.float32),
                "chunk_mask": pooled["chunk_mask"].reshape(1, chunk_count),
            })
        engine = {"logits": logits.reshape(-1)[:option_count].tolist(), "kinetic": float(kinetic.reshape(-1)[0]),
                  "nearest": float(nearest.reshape(-1)[0]), "steps": int(steps.reshape(-1)[0]),
                  "gates": gates.reshape(-1).tolist()}
        return vega.answer(rendered, vega.decide_one(self.config, rendered, engine)), length


def _load() -> Decider:
    from huggingface_hub import snapshot_download

    roles.require("decide", model_name)
    return Decider(_real_files(Path(snapshot_download(model_name, allow_patterns=roles.files("decide"))) / RELEASE))


model = workers.Lazy(_load)
executor = workers.ModelExecutor("decide-model", max_pending=8)
app = workers.app(model, model_executor=executor)


def _decide_all(state_text: str, rendered: dict[str, dict], picture_bytes: bytes | None) -> dict:
    answers = {}
    prompt_tokens = 0
    with model.use() as decider:
        image = None
        if picture_bytes is not None:
            from PIL import Image

            with Image.open(io.BytesIO(picture_bytes)) as picture:
                image = decider.see(picture)
            state_text = vega.VISION_STATE
        for question_id, item in rendered.items():
            try:
                answers[question_id], length = decider.decide(state_text, item, image)
            except ValueError as error:
                raise HTTPException(400, f"questions.{question_id}: {error}") from error
            prompt_tokens += length
    return {"model": model_name, "answers": answers,
            "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": 0, "total_tokens": prompt_tokens}}


@app.get("/health")
def health() -> dict:
    return {"ok": True, "model": model_name, "loaded": model.loaded, "busy": executor.pending}


@app.post("/v1/systemone")
async def systemone(payload: dict) -> dict:
    if not schemas.model_matches(payload.get("model"), model_name, "decide"):
        raise HTTPException(400, f"model: this worker serves only {model_name}")
    questions = payload.get("questions")
    if not isinstance(questions, dict) or not questions:
        raise HTTPException(400, "questions: a non-empty map of id -> question")
    if len(questions) > MAX_QUESTIONS:
        raise HTTPException(400, f"questions: at most {MAX_QUESTIONS} per request")
    image = payload.get("image")
    if image is not None and not isinstance(image, str):
        raise HTTPException(400, "image: a base64 string or data URL")
    if image is None and "state" not in payload:
        raise HTTPException(400, "state is required, or an image to read instead")
    picture_bytes = await asyncio.to_thread(pictures.decode, image) if image is not None else None
    state_text = vega.serialize_state(payload.get("state", ""))
    if len(state_text) > MAX_STATE_CHARS:
        raise HTTPException(400, f"state: at most {MAX_STATE_CHARS} characters")
    try:
        rendered = {question_id: vega.question(spec) for question_id, spec in questions.items()}
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return await executor.run_request(_decide_all, state_text, rendered, picture_bytes)
