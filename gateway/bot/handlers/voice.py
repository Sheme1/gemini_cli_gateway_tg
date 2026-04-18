from __future__ import annotations

import base64
import logging
from typing import Any

import aiohttp
from aiogram import F, Router
from aiogram.types import Message

from gateway.bot.handlers.messages import process_gemini_prompt
from gateway.config import Config
from gateway.gemini.session import SessionManager
from gateway.user_settings import UserSettingsStore

logger = logging.getLogger(__name__)
router = Router(name="voice")


async def transcribe_voice(
    audio_bytes: bytes, api_key: str, model: str = "gemini-2.5-flash"
) -> str:
    """Транскрибация аудио через Gemini API."""
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={api_key}"
    )

    b64_audio = base64.b64encode(audio_bytes).decode("utf-8")
    payload = {
        "contents": [
            {
                "parts": [
                    {
                        "text": (
                            "Пожалуйста, сделай транскрибацию этого аудиосообщения. "
                            "Отправь только текст транскрибации, без комментариев."
                        )
                    },
                    {"inlineData": {"mimeType": "audio/ogg", "data": b64_audio}},
                ]
            }
        ]
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(
            url,
            json=payload,
            headers={"Content-Type": "application/json"},
        ) as response:
            if response.status != 200:
                text = await response.text()
                raise Exception(
                    f"Ошибка Gemini API: код {response.status}. Ответ: {text}"
                )

            data = await response.json()
            try:
                text = data["candidates"][0]["content"]["parts"][0]["text"]
                return text.strip()
            except (KeyError, IndexError) as exc:
                raise Exception(
                    "Не удалось разобрать ответ Gemini API при транскрибации."
                ) from exc


@router.message(F.voice)
async def voice_handler(
    message: Message,
    session_manager: SessionManager,
    config: Config,
    bot: Any,
    user_settings: UserSettingsStore,
) -> None:
    """Обрабатывает голосовые сообщения: транскрибирует и передает в Gemini CLI."""
    if not config.gemini_api_key:
        await message.reply(
            "❌ Для голосовых сообщений требуется GEMINI_API_KEY в файле .env."
        )
        return

    chat_id = message.chat.id
    status_message = await message.reply("🎙 Скачиваю голосовое сообщение...")

    try:
        file = await bot.get_file(message.voice.file_id)
        audio_stream = await bot.download_file(file.file_path)
        audio_bytes = audio_stream.read()

        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=status_message.message_id,
            text="🎙 Распознаю речь через Gemini API...",
        )

        transcription = await transcribe_voice(
            audio_bytes,
            config.gemini_api_key,
            "gemini-2.5-flash",
        )
        prompt_info = f"🎙 Расшифровка:\n{transcription}\n\n"

        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=status_message.message_id,
            text=prompt_info,
        )

        await process_gemini_prompt(
            bot=bot,
            chat_id=chat_id,
            user_id=message.from_user.id,
            prompt=transcription,
            session_manager=session_manager,
            config=config,
            user_settings=user_settings,
            initial_message_id=status_message.message_id,
            initial_text=prompt_info,
        )
    except Exception as exc:
        logger.error("Voice handler error: %s", exc, exc_info=True)
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=status_message.message_id,
            text=f"❌ Ошибка обработки голосового сообщения: {exc}",
        )
