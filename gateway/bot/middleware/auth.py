from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject


class AuthMiddleware(BaseMiddleware):
    """
    Middleware для проверки доступа к боту по TARGET_CHAT_ID.
    Блокирует сообщения от неавторизованных чатов.
    """
    
    def __init__(self, target_chat_id: int | None = None):
        super().__init__()
        self.target_chat_id = target_chat_id

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        
        # Разрешаем доступ если TARGET_CHAT_ID не задан
        if self.target_chat_id is None:
            return await handler(event, data)
            
        # Проверяем откуда пришло событие
        if isinstance(event, Message):
            if event.chat.id != self.target_chat_id:
                # Опционально: можно логировать или отправлять предупреждение
                # await event.answer("⚠️ Доступ запрещен. Бот привязан к другому чату.")
                return
                
        return await handler(event, data)
