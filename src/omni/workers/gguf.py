"""Shared by the diffusers workers (image, video): pick a GGUF transformer and the checkpoint behind it."""
from huggingface_hub import hf_hub_download, snapshot_download

from omni.registry import huggingface


def source(role: str, repo: str) -> tuple[str | None, str | None]:
    """Local paths for a GGUF bundle: the transformer file and the diffusers folder with its text
    encoder and VAE; (None, None) for a plain diffusers checkpoint.

    Read from the manifest the download wrote, so a cached model loads offline. A model nobody
    pulled first is resolved against the Hub once, and remembered.
    """
    parts = huggingface.load_manifest(role, repo)
    if parts is None:
        parts = huggingface.bundle(role, repo)
        huggingface.save_manifest(role, repo, parts)
    main, *rest = parts
    if not rest or not main.files or not main.files[0].endswith(".gguf"):
        return None, None
    base = rest[0]
    return _fetch(lambda **kw: hf_hub_download(repo, main.files[0], **kw)), \
        _fetch(lambda **kw: snapshot_download(base.repo, allow_patterns=base.files, **kw))


def _fetch(download) -> str:
    """The cached copy when there is one, so no Hub call is made; else download it."""
    try:
        return download(local_files_only=True)
    except Exception:  # noqa: BLE001 - not cached (or incomplete): fetch it
        return download()


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

