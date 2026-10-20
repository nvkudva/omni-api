"""One local gateway for chat, decide, embed, speech and media models."""
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _metadata_version


def version() -> str | None:
    """The installed omni-api version, or None when running from a checkout that was never built."""
    try:
        return _metadata_version("omni-api")
    except PackageNotFoundError:
        return None
