import asyncio
import argparse
import logging
import sys
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramConflictError
from aiogram.types import BotCommand

from gateway.bot.handlers import callbacks, commands, errors, messages, voice
from gateway.bot.middleware.auth import AuthMiddleware
from gateway.config import Config
from gateway.gemini.session import SessionManager
from gateway.runtime import GatewayRuntimeState, build_status_text, startup_preflight
from gateway.user_settings import UserSettingsStore

logger = logging.getLogger(__name__)


def configure_logging(level_name: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level_name.upper(), logging.INFO),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        stream=sys.stdout,
    )


async def main(check_runtime: bool = False) -> None:
    configure_logging()

    try:
        config = Config.from_env()
    except Exception as e:
        logger.error(f"Ошибка загрузки конфигурации: {e}")
        sys.exit(1)

    configured_level = getattr(logging, config.log_level, logging.INFO)
    if config.gemini_stream_debug and configured_level > logging.INFO:
        configured_level = logging.INFO
    logging.getLogger().setLevel(configured_level)

    if config.gemini_stream_debug:
        logger.info("Gemini stream diagnostics enabled.")

    runtime_state = GatewayRuntimeState()

    # Инициализация бота
    bot = Bot(
        token=config.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    # Инициализация SessionManager
    session_manager = SessionManager(config=config, runtime_state=runtime_state)
    user_settings = UserSettingsStore(state_dir=Path(config.gateway_state_dir))

    try:
        await startup_preflight(config, bot, runtime_state)
    except Exception as exc:
        runtime_state.record_error(exc, context="startup preflight")
        logger.error("Startup preflight failed: %s", exc, exc_info=True)
        await bot.session.close()
        sys.exit(1)

    if check_runtime:
        print(
            await build_status_text(
                config,
                runtime_state,
                session_manager,
                bot=bot,
                refresh_webhook=False,
            )
        )
        await bot.session.close()
        return

    # Регистрация меню команд
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Запуск бота / Главное меню"),
            BotCommand(command="new", description="🔄 Новый диалог (сброс контекста)"),
            BotCommand(command="sessions", description="📂 Загрузить прошлую сессию"),
            BotCommand(command="mcp", description="🔌 Управление MCP серверами"),
            BotCommand(command="skills", description="🧠 Управление навыками"),
            BotCommand(command="model", description="Выбрать модель Gemini"),
            BotCommand(command="settings", description="Настройки бота и вывода"),
            BotCommand(command="status", description="Статус шлюза Gemini"),
            BotCommand(command="diagnostics", description="Диагностика шлюза"),
            BotCommand(command="cancel", description="Остановить текущий запрос"),
            BotCommand(command="help", description="Справка"),
        ]
    )

    dp = Dispatcher(
        session_manager=session_manager,
        config=config,
        user_settings=user_settings,
        runtime_state=runtime_state,
    )

    # Регистрация middlewares
    auth_middleware = AuthMiddleware(target_chat_id=config.target_chat_id)
    dp.message.middleware(auth_middleware)
    dp.callback_query.middleware(auth_middleware)

    # Регистрация роутеров
    dp.include_router(errors.router)
    dp.include_router(callbacks.router)
    dp.include_router(commands.router)
    dp.include_router(messages.router)
    dp.include_router(voice.router)

    logger.info("Bot started. Listening for messages...")
    try:
        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types(),
            polling_timeout=config.polling_timeout,
            tasks_concurrency_limit=config.polling_concurrency_limit,
        )
    except TelegramConflictError as exc:
        runtime_state.record_error(exc, context="polling conflict")
        logger.error(
            "Telegram polling conflict. Another bot instance or webhook is active: %s",
            exc,
        )
        raise
    finally:
        logger.info("Bot stopped.")
        await session_manager.kill()
        await bot.session.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check-runtime",
        action="store_true",
        help="Run startup diagnostics and exit without polling.",
    )
    args = parser.parse_args()

    try:
        asyncio.run(main(check_runtime=args.check_runtime))
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutting down gracefully.")
