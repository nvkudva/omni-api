"""Shared by the diffusers workers (image, video): pick a GGUF transformer and the checkpoint behind it."""
import json
from pathlib import Path

from omni.registry import huggingface


def find_file(repo: str) -> str | None:
    """The repo's 8-bit GGUF, when it ships one: a quantized checkpoint needs no extra config."""
    try:
        from huggingface_hub import HfApi

        return huggingface.preferred_gguf(HfApi().list_repo_files(repo))
    except Exception:  # noqa: BLE001 - an offline hub falls through to the plain load, which reports it
        return None


def base_repo(repo: str) -> str:
    """The checkpoint a GGUF quantizes: the card's `base_model`, else the repo itself."""
    try:
        from huggingface_hub import HfApi

        base = (HfApi().model_info(repo).cardData or {}).get("base_model")
    except Exception:  # noqa: BLE001
        return repo
    if isinstance(base, list):
        base = base[0] if base else None
    return base if isinstance(base, str) and base else repo


def diffusers_files(repo: str) -> tuple[str, list[str]]:
    """The diffusers repo to use and the files of it a GGUF load reads: everything but the transformer weights.

    The GGUF replaces the transformer, so its bf16 shards are never read; the repo's own
    single-file checkpoints (LTX-Video ships several) are skipped the same way.
    """
    from huggingface_hub import hf_hub_download

    try:
        index = json.loads(Path(hf_hub_download(repo, "model_index.json")).read_text())
    except Exception:  # noqa: BLE001 - a card's base_model may name the original, not the diffusers conversion
        repo = f"{repo}-Diffusers"
        index = json.loads(Path(hf_hub_download(repo, "model_index.json")).read_text())
    others = [name for name, value in index.items() if isinstance(value, list) and value[0] and name != "transformer"]
    return repo, ["model_index.json", "transformer/config.json", *(f"{name}/*" for name in others)]


def dequantize_vectors(model) -> None:
    """Replace the 1-D GGUF tensors (norms' scales) with plain ones.

    The pinned diffusers dequantizes only the 2-D weights it swaps for GGUF linears, so a norm's
    scale stays a byte-shaped `GGUFParameter` and every forward fails on its size.
    """
    import torch
    from diffusers.quantizers.gguf.utils import GGUFParameter, dequantize_gguf_tensor

    for module in model.modules():
        for name, parameter in list(module.named_parameters(recurse=False)):
            if isinstance(parameter, GGUFParameter) and parameter.dim() == 1:
                module._parameters[name] = torch.nn.Parameter(
                    dequantize_gguf_tensor(parameter).to(torch.bfloat16), requires_grad=False)

