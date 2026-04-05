from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


def get_models_keyboard(current_model: str) -> InlineKeyboardMarkup:
    """Клавиатура для выбора модели Gemini."""
    models = [
        "gemini-3-flash-preview",
        "gemini-3.1-pro-preview",
        "gemini-3.1-flash-lite-preview",
        "gemini-2.5-flash",
        "gemini-2.5-pro",
    ]

    builder = InlineKeyboardBuilder()
    for model in models:
        text = f"✅ {model}" if model == current_model else model
        builder.button(text=text, callback_data=f"model:{model}")

    builder.adjust(1)  # По одной кнопке в ряд
    return builder.as_markup()


def get_settings_keyboard(
    approval_mode: str, timeout: int, sandbox: bool
) -> InlineKeyboardMarkup:
    """Клавиатура для настроек."""
    builder = InlineKeyboardBuilder()

    builder.button(
        text=f"Режим аппрува: {approval_mode}", callback_data="settings:approval"
    )
    builder.button(text=f"Таймаут (сек): {timeout}", callback_data="settings:timeout")
    builder.button(
        text=f"Sandbox: {'✅' if sandbox else '❌'}", callback_data="settings:sandbox"
    )

    builder.adjust(1)
    return builder.as_markup()


def get_approval_modes_keyboard(current_mode: str) -> InlineKeyboardMarkup:
    """Клавиатура выбора режима --approval-mode."""
    modes = ["default", "auto_edit", "yolo", "plan"]
    builder = InlineKeyboardBuilder()

    for mode in modes:
        text = f"✅ {mode}" if mode == current_mode else mode
        builder.button(text=text, callback_data=f"set_approval:{mode}")

    builder.button(text="🔙 Назад", callback_data="settings:main")
    builder.adjust(2, 2, 1)
    return builder.as_markup()


def get_interactive_approval_keyboard() -> InlineKeyboardMarkup:
    """Кнопки для подтверждения действия Gemini CLI."""
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Одобрить (y)", callback_data="approve:yes")
    builder.button(text="❌ Отклонить (n)", callback_data="approve:no")
    builder.button(text="⏭ YOLO (всё одобрить)", callback_data="approve:yolo")
    builder.adjust(2, 1)
    return builder.as_markup()

def get_mcp_list_keyboard(servers: list[tuple[str, bool]]) -> InlineKeyboardMarkup:
    """Клавиатура со списком MCP серверов и кнопками для их Вкл/Выкл."""
    builder = InlineKeyboardBuilder()
    
    for name, is_enabled in servers:
        status = "🟢" if is_enabled else "🔴"
        action = "disable" if is_enabled else "enable"
        # callback_data: "mcp_toggle:<name>:<action>"
        builder.button(
            text=f"{status} {name}", 
            callback_data=f"mcp_toggle:{name}:{action}"
        )
    
    builder.adjust(1)
    # Добавляем кнопку обновления
    builder.row(InlineKeyboardButton(text="🔄 Обновить список", callback_data="mcp_refresh"))
    return builder.as_markup()

def get_skills_list_keyboard(skills: list[tuple[str, bool]]) -> InlineKeyboardMarkup:
    """Клавиатура со списком Skills и кнопками для их Вкл/Выкл."""
    builder = InlineKeyboardBuilder()
    
    for name, is_enabled in skills:
        status = "🟢" if is_enabled else "🔴"
        action = "disable" if is_enabled else "enable"
        # callback_data: "skill_toggle:<name>:<action>"
        builder.button(
            text=f"{status} {name}", 
            callback_data=f"skill_toggle:{name}:{action}"
        )
    
    builder.adjust(1)
    # Добавляем кнопку обновления
    builder.row(InlineKeyboardButton(text="🔄 Обновить список", callback_data="skill_refresh"))
    return builder.as_markup()
