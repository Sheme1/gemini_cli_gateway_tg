import logging
from typing import Any

from aiogram import F, Router
from aiogram.types import Message

from gateway.bot.keyboards import inline
from gateway.config import Config
from gateway.gemini.session import SessionManager
from gateway.streaming.editor import StreamEditor

logger = logging.getLogger(__name__)
router = Router(name="messages")


@router.message(F.text)
async def message_handler(
    message: Message, session_manager: SessionManager, config: Config, bot: Any
) -> None:
    """Обрабатывает текстовые сообщения и шлет их в Gemini CLI."""
    prompt = message.text
    if not prompt:
        return

    chat_id = message.chat.id

    # Добавляем контекст для reply-сообщений
    if message.reply_to_message and message.reply_to_message.text:
        reply_username = (
            "Bot"
            if message.reply_to_message.from_user.is_bot
            else message.reply_to_message.from_user.full_name
        )
        prompt = (
            f"Контекст: это ответ на сообщение от {reply_username}:\n"
            f"> {message.reply_to_message.text}\n\n"
            f"Текущий ответ от {message.from_user.full_name}:\n{prompt}"
        )

    # Инициализация стримера
    streamer = StreamEditor(
        bot=bot,
        chat_id=chat_id,
        interval=config.stream_update_interval,
        max_length=config.stream_max_message_length,
    )
    await streamer.initialize("⏳ <i>Генерирую ответ...</i>")

    async def on_chunk(text: str) -> None:
        await streamer.append_text(text)

    async def on_approval(req: dict) -> None:
        logger.info(f"Получен запрос на аппрув: {req}")
        tool_name = req.get("tool", "Unknown Action")
        # Сначала доставляем всё, что было до этого
        await streamer.flush()

        # Отправляем сообщение для подтверждения с кнопками
        await bot.send_message(
            chat_id=chat_id,
            text=f"⚠️ <b>Gemini запрашивает подтверждение действия:</b>\n"
            f"Действие: <code>{tool_name}</code>\n\n"
            f"Что делать?",
            reply_markup=inline.get_interactive_approval_keyboard(),
        )
        # Session manager останется ждать ответа, пока не дернут callback_interactive_approve

    try:
        await session_manager.send_prompt(
            prompt=prompt, on_chunk=on_chunk, on_approval=on_approval
        )
    except Exception as e:
        logger.error(f"Ошибка при процессинге промпта: {e}", exc_info=True)
        await streamer.append_text(f"\n\n❌ Ошибка: {str(e)}")
    finally:
        # В любом случае добиваем буфер до Telegram
        await streamer.flush()
