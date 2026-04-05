import base64
import logging
from typing import Any

from aiogram import F, Router
from aiogram.types import Message, FSInputFile
import aiohttp

from gateway.config import Config
from gateway.gemini.session import SessionManager
from gateway.streaming.editor import StreamEditor

logger = logging.getLogger(__name__)
router = Router(name="voice")


async def transcribe_voice(
    audio_bytes: bytes, api_key: str, model: str = "gemini-2.5-flash"
) -> str:
    """Транскрибация аудио через Gemini API, возвращает текст."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"

    b64_audio = base64.b64encode(audio_bytes).decode("utf-8")

    payload = {
        "contents": [
            {
                "parts": [
                    {
                        "text": "Пожалуйста, сделай транскрибацию этого аудиосообщения."
                        "Отправь только текст транскрибации, без каких-либо оберток и комментариев."
                    },
                    {"inlineData": {"mimeType": "audio/ogg", "data": b64_audio}},
                ]
            }
        ]
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(
            url, json=payload, headers={"Content-Type": "application/json"}
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise Exception(f"Gemini API error: {resp.status} - {text}")

            data = await resp.json()
            try:
                # Извлекаем текст
                text = data["candidates"][0]["content"]["parts"][0]["text"]
                return text.strip()
            except (KeyError, IndexError):
                raise Exception("Failed to parse response from Gemini API")


@router.message(F.voice)
async def voice_handler(
    message: Message, session_manager: SessionManager, config: Config, bot: Any
) -> None:
    """Обрабатывает голосовые сообщения: транскрибирует и отсылает как промпт."""
    if not config.gemini_api_key:
        await message.reply(
            "❌ Для голосовых сообщений требуется GEMINI_API_KEY в .env"
        )
        return

    chat_id = message.chat.id
    msg = await message.reply("🎙 <i>Скачиваю голосовое...</i>")

    try:
        # Скачиваем аудио в BytesIO (в памяти)
        file = await bot.get_file(message.voice.file_id)
        audio_stream = await bot.download_file(file.file_path)
        audio_bytes = audio_stream.read()

        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=msg.message_id,
            text="🎙 <i>Распознаю речь (Gemini API)...</i>",
            parse_mode="HTML",
        )

        # Транскрибируем
        transcription = await transcribe_voice(
            audio_bytes, config.gemini_api_key, "gemini-2.5-flash"
        )

        prompt_info = f"🎙 <b>Транскрибация:</b>\n{transcription}\n\n"
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=msg.message_id,
            text=prompt_info + "⏳ <i>Генерирую ответ...</i>",
            parse_mode="HTML",
        )

        # Инициализируем стример, базируясь на уже отправленном сообщении
        streamer = StreamEditor(
            bot=bot,
            chat_id=chat_id,
            interval=config.stream_update_interval,
            max_length=config.stream_max_message_length,
        )
        # Подключаемся к существующему сообщению
        streamer.current_message_id = msg.message_id
        streamer.last_sent_text = prompt_info
        streamer._first_chunk = False  # Не заменяем, а дописываем к транскрибации

        async def on_chunk(text: str) -> None:
            await streamer.append_text(text)

        async def on_approval(req: dict) -> None:
            from gateway.bot.keyboards import inline

            logger.info(f"Получен запрос на аппрув: {req}")
            tool_name = req.get("tool", "Unknown Action")
            await streamer.flush()
            await bot.send_message(
                chat_id=chat_id,
                text=f"⚠️ <b>Gemini запрашивает подтверждение действия:</b>\n"
                f"Действие: <code>{tool_name}</code>\n\n"
                f"Что делать?",
                reply_markup=inline.get_interactive_approval_keyboard(),
            )

        async def on_file(filepath: str) -> None:
            logger.info(f"Отправка сгенерированного файла: {filepath}")
            try:
                import os
                if os.path.exists(filepath):
                    await bot.send_document(
                        chat_id=chat_id,
                        document=FSInputFile(filepath),
                        caption="📎 ИИ сгенерировал и вложил этот файл."
                    )
                else:
                    await streamer.append_text(f"\n\n❌ Ошибка: Файл {filepath} не найден на диске.")
            except Exception as e:
                logger.error(f"Не удалось отправить файл {filepath}: {e}")
                await streamer.append_text(f"\n\n❌ Ошибка отправки файла: {e}")

        await session_manager.send_prompt(
            prompt=transcription,
            user_id=message.from_user.id,
            on_chunk=on_chunk,
            on_approval=on_approval,
            on_file=on_file,
        )
        await streamer.flush()

    except Exception as e:
        logger.error(f"Voice handler error: {e}", exc_info=True)
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=msg.message_id,
            text=f"❌ Ошибка обработки голосового: {e}",
        )
