from __future__ import annotations

import logging
from typing import Any

from aiogram import Router
from aiogram.types import ErrorEvent, Update

from gateway.runtime import GatewayRuntimeState

logger = logging.getLogger(__name__)
router = Router(name="errors")


@router.errors()
async def global_error_handler(
    event: ErrorEvent,
    runtime_state: GatewayRuntimeState | None = None,
) -> None:
    context = _update_context(event.update)
    if runtime_state:
        runtime_state.record_error(event.exception, context=context)
    logger.error(
        "Unhandled update error: %s",
        context,
        exc_info=(
            type(event.exception),
            event.exception,
            event.exception.__traceback__,
        ),
    )


def _update_context(update: Update) -> str:
    payload: dict[str, Any] = {"update_id": update.update_id}

    message = update.message or update.edited_message
    callback = update.callback_query
    if message:
        payload["chat_id"] = message.chat.id
        if message.from_user:
            payload["user_id"] = message.from_user.id
    elif callback:
        payload["callback_id"] = callback.id
        payload["user_id"] = callback.from_user.id
        if callback.message:
            payload["chat_id"] = callback.message.chat.id

    return " ".join(f"{key}={value}" for key, value in payload.items())
