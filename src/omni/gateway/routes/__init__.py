"""The gateway's route families. Each `register(app, ctx)` attaches its endpoints."""
from omni.gateway.routes import admin, chat, media, model_calls, speech, system

MODULES = (system, chat, model_calls, media, speech, admin)

__all__ = ["MODULES"]
