from aiogram import Router, html
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from gateway.bot.keyboards import inline
from gateway.config import Config
from gateway.gemini.session import SessionManager

router = Router(name="commands")


@router.message(CommandStart())
async def command_start_handler(
    message: Message, session_manager: SessionManager
) -> None:
    """Обработчик команды /start"""
    text = (
        f"🤖 Добро пожаловать, {html.bold(message.from_user.full_name)}!\n\n"
        f"Это шлюз к Gemini CLI (Headless Mode).\n"
        f"Отправьте любое сообщение, чтобы начать диалог.\n\n"
        f"Команды:\n"
        f"🔄 /new — начать новый диалог (очистить контекст)\n"
        f"📂 /sessions — список прошлых диалогов\n"
        f"ℹ️ /help — справка"
    )
    # Начинаем новую сессию
    await session_manager.reset(message.from_user.id)
    await message.answer(text)


@router.message(Command("new"))
async def command_new_handler(
    message: Message, session_manager: SessionManager
) -> None:
    """Обработчик команды /new — сброс контекста Gemini."""
    await session_manager.reset(message.from_user.id)
    await message.answer("✅ Контекст полностью очищен. Следующее сообщение начнет новый диалог.")


@router.message(Command("sessions"))
async def command_sessions_handler(
    message: Message, session_manager: SessionManager
) -> None:
    """Обработчик команды /sessions."""
    await message.answer("⏳ <i>Запрашиваю список сессий...</i>", parse_mode="HTML")
    try:
        sessions = await session_manager.get_sessions_list()
        if not sessions:
            await message.answer("📂 Нет сохраненных сессий.")
            return

        from aiogram.utils.keyboard import InlineKeyboardBuilder
        from aiogram.types import InlineKeyboardButton
        
        builder = InlineKeyboardBuilder()
        text_lines = ["📂 <b>Доступные сессии:</b>\n"]
        for idx, (s_id, desc) in enumerate(sessions, 1):
            text_lines.append(f"{idx}. <code>{s_id}</code>\n   {html.quote(desc)}")
            builder.row(InlineKeyboardButton(text=f"Открыть #{idx} ({s_id[:6]})", callback_data=f"resume_{s_id}"))

        await message.answer("\n".join(text_lines), reply_markup=builder.as_markup())
    except Exception as e:
        await message.answer(f"❌ Ошибка при получении сессий: {e}")


@router.message(Command("status"))
async def command_status_handler(
    message: Message, session_manager: SessionManager
) -> None:
    """Обработчик команды /status."""
    status_text = "🟢 Gemini CLI: шлюз работает (Headless режим)."
    await message.answer(status_text)


@router.message(Command("model"))
async def command_model_handler(message: Message, config: Config) -> None:
    """Обработчик команды /model — выбор модели Gemini."""
    await message.answer(
        "Выберите модель Gemini:",
        reply_markup=inline.get_models_keyboard(config.gemini_model),
    )


@router.message(Command("settings"))
async def command_settings_handler(message: Message, config: Config) -> None:
    """Обработчик команды /settings — настройки CLI."""
    await message.answer(
        "⚙️ <b>Настройки Gemini CLI</b>",
        reply_markup=inline.get_settings_keyboard(
            approval_mode=config.gemini_approval_mode,
            timeout=config.gemini_cli_timeout,
            sandbox=config.gemini_sandbox,
        ),
    )


@router.message(Command("help"))
async def command_help_handler(message: Message) -> None:
    """Обработчик команды /help."""
    text = (
        "📖 <b>Справка по Gemini Gateway V2</b>\n\n"
        "Бот транслирует ваши сообщения в Gemini CLI.\n"
        "Контекст переписки сохраняется автоматически.\n\n"
        "<b>Доступные команды:</b>\n"
        "/new - Очистить историю и сбросить сессию\n"
        "/sessions - Загрузить предыдущие диалоги\n"
        "/settings - Настройки\n"
    )
    await message.answer(text)
