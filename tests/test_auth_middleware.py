from datetime import UTC, datetime

import pytest
from aiogram.types import CallbackQuery, Chat, Message, User

from gateway.bot.middleware.auth import AuthMiddleware


def _message(chat_id: int) -> Message:
    return Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=chat_id, type="private"),
    )


def _callback(chat_id: int) -> CallbackQuery:
    return CallbackQuery(
        id="callback-id",
        from_user=User(id=123, is_bot=False, first_name="Test"),
        chat_instance="chat-instance",
        message=_message(chat_id),
        data="noop",
    )


@pytest.mark.asyncio
async def test_auth_middleware_allows_any_configured_chat_id() -> None:
    middleware = AuthMiddleware(target_chat_ids=(111, 222))
    calls = []

    async def handler(event, _data):
        calls.append(event)
        return "ok"

    assert await middleware(handler, _message(111), {}) == "ok"
    assert await middleware(handler, _callback(222), {}) == "ok"
    assert await middleware(handler, _message(333), {}) is None
    assert await middleware(handler, _callback(333), {}) is None
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_auth_middleware_keeps_empty_allowlist_open() -> None:
    middleware = AuthMiddleware()
    calls = []

    async def handler(event, _data):
        calls.append(event)
        return "ok"

    assert await middleware(handler, _message(333), {}) == "ok"
    assert len(calls) == 1
