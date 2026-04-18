from __future__ import annotations

import logging
import time
from typing import Any

from aiogram import F, Router
from aiogram.types import Message

from gateway.artifacts import ArtifactManager
from gateway.bot.keyboards import inline
from gateway.config import Config
from gateway.gemini.renderer import render_event
from gateway.gemini.session import SessionManager
from gateway.streaming.editor import StreamEditor
from gateway.user_settings import UserSettingsStore

logger = logging.getLogger(__name__)
router = Router(name="messages")


@router.message(F.text)
async def message_handler(
    message: Message,
    session_manager: SessionManager,
    config: Config,
    bot: Any,
    user_settings: UserSettingsStore,
) -> None:
    """Обрабатывает текстовые сообщения и отправляет их в Gemini CLI."""
    prompt = message.text
    if not prompt:
        return

    chat_id = message.chat.id
    user_id = message.from_user.id

    if message.reply_to_message and message.reply_to_message.text:
        reply_username = (
            "бота"
            if message.reply_to_message.from_user.is_bot
            else message.reply_to_message.from_user.full_name
        )
        prompt = (
            f"Контекст: это ответ на сообщение от {reply_username}:\n"
            f"> {message.reply_to_message.text}\n\n"
            f"Текущий ответ от {message.from_user.full_name}:\n{prompt}"
        )

    await process_gemini_prompt(
        bot=bot,
        chat_id=chat_id,
        user_id=user_id,
        prompt=prompt,
        session_manager=session_manager,
        config=config,
        user_settings=user_settings,
    )


async def process_gemini_prompt(
    bot: Any,
    chat_id: int,
    user_id: int,
    prompt: str,
    session_manager: SessionManager,
    config: Config,
    user_settings: UserSettingsStore,
    initial_message_id: int | None = None,
    initial_text: str = "",
) -> None:
    """Общий pipeline потокового вывода для любых входящих запросов."""
    streamer = StreamEditor(
        bot=bot,
        chat_id=chat_id,
        interval=config.stream_update_interval,
        max_length=config.stream_max_message_length,
    )
    artifact_manager = ArtifactManager(config)
    render_mode = user_settings.get_render_mode(user_id)
    started_at = time.time()

    if initial_message_id is None:
        await streamer.initialize("⏳ Генерирую ответ...")
    else:
        streamer.attach_to_message(initial_message_id, initial_text)

    async def on_event(event) -> None:
        artifact_manager.register_event(event)
        rendered = render_event(event, render_mode)
        if rendered:
            await streamer.append_text(rendered)

    async def on_approval(req: dict) -> None:
        logger.info("Получен запрос на подтверждение: %s", req)
        tool_name = (
            req.get("tool")
            or req.get("name")
            or req.get("action")
            or "неизвестное действие"
        )
        await streamer.flush()
        await bot.send_message(
            chat_id=chat_id,
            text=(
                "⚠️ Gemini запрашивает подтверждение действия.\n"
                f"Действие: {tool_name}\n\n"
                "Что делать?"
            ),
            reply_markup=inline.get_interactive_approval_keyboard(),
        )

    try:
        await session_manager.send_prompt(
            prompt=prompt,
            user_id=user_id,
            on_event=on_event,
            on_approval=on_approval,
        )
    except Exception as exc:
        logger.error("Ошибка при обработке запроса: %s", exc, exc_info=True)
        await streamer.append_text(f"\n\n⚠️ Ошибка: {exc}")
    finally:
        await streamer.flush()
        try:
            await artifact_manager.send_artifacts(
                bot=bot,
                chat_id=chat_id,
                started_at=started_at,
            )
        except Exception as exc:
            logger.error("Ошибка при отправке артефактов: %s", exc, exc_info=True)
            await bot.send_message(
                chat_id=chat_id,
                text=f"⚠️ Не удалось отправить сгенерированный файл: {exc}",
            )
