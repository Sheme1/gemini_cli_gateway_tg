import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from gateway.bot.handlers import callbacks, commands, messages, voice
from gateway.bot.middleware.auth import AuthMiddleware
from gateway.config import Config
from gateway.gemini.session import SessionManager

logger = logging.getLogger(__name__)


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        stream=sys.stdout,
    )

    try:
        config = Config.from_env()
    except Exception as e:
        logger.error(f"Ошибка загрузки конфигурации: {e}")
        sys.exit(1)

    # Инициализация бота
    bot = Bot(
        token=config.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    # Инициализация SessionManager
    session_manager = SessionManager(config=config)

    # Регистрация меню команд
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Запуск бота / Главное меню"),
            BotCommand(command="new", description="🔄 Новый диалог (сброс контекста)"),
            BotCommand(command="sessions", description="📂 Загрузить прошлую сессию"),
            BotCommand(command="mcp", description="🔌 Управление MCP серверами"),
            BotCommand(command="skills", description="🧠 Управление Skills"),
            BotCommand(command="model", description="Выбрать модель Gemini"),
            BotCommand(command="settings", description="Настройки Gemini CLI"),
            BotCommand(command="status", description="Статус сессии Gemini"),
            BotCommand(command="help", description="Справка"),
        ]
    )

    dp = Dispatcher(
        session_manager=session_manager,
        config=config,
    )

    # Регистрация middlewares
    auth_middleware = AuthMiddleware(target_chat_id=config.target_chat_id)
    dp.message.middleware(auth_middleware)

    # Регистрация роутеров
    dp.include_router(callbacks.router)
    dp.include_router(commands.router)
    dp.include_router(messages.router)
    dp.include_router(voice.router)

    logger.info("Bot started. Listening for messages...")
    try:
        await dp.start_polling(bot)
    finally:
        logger.info("Bot stopped.")
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutting down gracefully.")
