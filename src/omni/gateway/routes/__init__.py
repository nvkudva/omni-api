"""The gateway's route families. Each `register(app, ctx)` attaches its endpoints."""
from omni.gateway.routes import (
    admin_models,
    admin_runtime,
    chat,
    control,
    media,
    model_calls,
    speech,
    system,
)

MODULES = (system, chat, model_calls, media, speech, admin_models, admin_runtime, control)

__all__ = ["MODULES"]
