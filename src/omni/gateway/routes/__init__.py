"""The gateway's route families. Each `register(app, ctx)` attaches its endpoints."""
from omni.gateway.routes import (
    admin_models,
    admin_runtime,
    chat,
    control,
    decisions,
    media,
    model_calls,
    speech,
    system,
)

MODULES = (system, chat, model_calls, decisions, media, speech, admin_models, admin_runtime, control)

__all__ = ["MODULES"]
