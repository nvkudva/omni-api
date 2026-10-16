"""The gateway's route families. Each `register(app, ctx)` attaches its endpoints."""
from omni.gateway.routes import admin, chat, control, media, model_calls, speech, system

MODULES = (system, chat, model_calls, media, speech, admin, control)

__all__ = ["MODULES"]
