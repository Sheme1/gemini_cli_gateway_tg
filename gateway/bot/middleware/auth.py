from collections.abc import Iterable
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject


class AuthMiddleware(BaseMiddleware):
    """
    Middleware для проверки доступа к боту по TARGET_CHAT_ID.
    Блокирует сообщения от неавторизованных чатов.
    """

    def __init__(
        self,
        target_chat_id: int | None = None,
        target_chat_ids: Iterable[int] | None = None,
    ):
        super().__init__()
        if target_chat_ids is not None:
            self.target_chat_ids = frozenset(
                int(chat_id) for chat_id in target_chat_ids
            )
        elif target_chat_id is not None:
            self.target_chat_ids = frozenset({target_chat_id})
        else:
            self.target_chat_ids = frozenset()

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:

        # Разрешаем доступ если TARGET_CHAT_ID не задан
        if not self.target_chat_ids:
            return await handler(event, data)

        # Проверяем откуда пришло событие
        if isinstance(event, Message):
            if event.chat.id not in self.target_chat_ids:
                # Опционально: можно логировать или отправлять предупреждение
                # await event.answer("⚠️ Доступ запрещен. Бот привязан к другому чату.")
                return
        elif isinstance(event, CallbackQuery) and event.message:
            if event.message.chat.id not in self.target_chat_ids:
                return

        return await handler(event, data)
