"""The gateway: one FastAPI app that fronts Ollama and the media/speech workers."""
from omni.gateway.app import create

__all__ = ["create"]
