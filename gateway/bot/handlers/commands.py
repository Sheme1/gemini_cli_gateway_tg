from aiogram import Router, html
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from gateway.bot.keyboards import inline
from gateway.config import Config
from gateway.gemini.session import SessionManager

router = Router(name="commands")

@router.message(CommandStart())
async def command_start_handler(message: Message, session_manager: SessionManager) -> None:
    """Обработчик команды /start"""
    text = (
        f"🤖 Добро пожаловать, {html.bold(message.from_user.full_name)}!\n\n"
        f"Это шлюз к Gemini CLI (Interactive Mode).\n"
        f"Отправьте любое сообщение, чтобы начать диалог.\n\n"
        f"Команды:\n"
        f"🔄 /new — начать новый диалог (очистить контекст)\n"
        f"📊 /status — статус процесса\n"
        f"ℹ️ /help — справка"
    )
    # Гарантируем, что процесс запущен
    if not await session_manager.is_alive():
        await session_manager.spawn(resume=True)
    
    await message.answer(text)

@router.message(Command("new"))
async def command_new_handler(message: Message, session_manager: SessionManager) -> None:
    """Обработчик команды /new — сброс контекста Gemini."""
    await message.answer("🔄 Перезапускаю Gemini CLI... Сброс контекста.")
    await session_manager.reset()
    await message.answer("✅ Контекст полностью очищен. Можно начинать новый диалог.")

@router.message(Command("status"))
async def command_status_handler(message: Message, session_manager: SessionManager) -> None:
    """Обработчик команды /status."""
    is_alive = await session_manager.is_alive()
    if is_alive:
        status_text = "🟢 Gemini CLI: Процесс запущен и ожидает ввода."
    else:
        status_text = "🔴 Gemini CLI: Процесс не запущен."
        
    await message.answer(status_text)

@router.message(Command("model"))
async def command_model_handler(message: Message, config: Config) -> None:
    """Обработчик команды /model — выбор модели Gemini."""
    await message.answer(
        "Выберите модель Gemini:",
        reply_markup=inline.get_models_keyboard(config.gemini_model)
    )

@router.message(Command("settings"))
async def command_settings_handler(message: Message, config: Config) -> None:
    """Обработчик команды /settings — настройки CLI."""
    await message.answer(
        "⚙️ <b>Настройки Gemini CLI</b>",
        reply_markup=inline.get_settings_keyboard(
            approval_mode=config.gemini_approval_mode,
            timeout=config.gemini_cli_timeout,
            sandbox=config.gemini_sandbox
        )
    )

@router.message(Command("help"))
async def command_help_handler(message: Message) -> None:
    """Обработчик команды /help."""
    text = (
        "📖 <b>Справка по Gemini Gateway V2</b>\n\n"
        "бот транслирует ваши сообщения в долгий процесс `gemini CLI`.\n"
        "Контекст переписки сохраняется до вызова команды /new.\n\n"
        "<b>Доступные команды:</b>\n"
        "/new - Очистить историю и сбросить сессию\n"
        "/status - Проверить статус процесса\n"
    )
    await message.answer(text)
