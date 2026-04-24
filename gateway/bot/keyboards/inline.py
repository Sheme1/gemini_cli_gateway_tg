from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from gateway.model_presets import DEFAULT_MODEL_PRESET, MODEL_PRESETS
from gateway.bot.ui import (
    APPROVAL_MODE_LABELS,
    RENDER_MODE_LABELS,
    get_approval_mode_label,
    get_render_mode_label,
)


def get_models_keyboard(
    current_model: str,
    current_preset: str = DEFAULT_MODEL_PRESET,
    fallback_model: str | None = None,
) -> InlineKeyboardMarkup:
    """Клавиатура для выбора модели Gemini."""
    builder = InlineKeyboardBuilder()
    env_model = fallback_model or current_model
    env_text = f"Из .env: {env_model}"
    if current_preset == DEFAULT_MODEL_PRESET:
        env_text = f"✅ {env_text}"
    builder.button(text=env_text, callback_data=f"model:{DEFAULT_MODEL_PRESET}")

    for preset in MODEL_PRESETS.values():
        text = f"{preset.label}: {preset.model}"
        if current_preset == preset.key or (
            current_preset == DEFAULT_MODEL_PRESET and current_model == preset.model
        ):
            text = f"✅ {text}"
        builder.button(text=text, callback_data=f"model:{preset.key}")

    builder.adjust(1)  # По одной кнопке в ряд
    return builder.as_markup()


def get_prompt_guard_keyboard(token: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="▶️ Отправить в Gemini", callback_data=f"prompt:confirm:{token}")
    builder.button(text="❌ Отменить", callback_data=f"prompt:cancel:{token}")
    builder.adjust(1)
    return builder.as_markup()


def get_settings_keyboard(render_mode: str, approval_mode: str) -> InlineKeyboardMarkup:
    """Клавиатура для настроек."""
    builder = InlineKeyboardBuilder()

    builder.button(
        text=f"Режим отображения: {get_render_mode_label(render_mode)}",
        callback_data="settings:render",
    )
    builder.button(
        text=f"Режим подтверждений: {get_approval_mode_label(approval_mode)}",
        callback_data="settings:approval",
    )

    builder.adjust(1)
    return builder.as_markup()


def get_render_modes_keyboard(current_mode: str) -> InlineKeyboardMarkup:
    """Клавиатура выбора режима отображения."""
    builder = InlineKeyboardBuilder()

    for mode in RENDER_MODE_LABELS:
        label = get_render_mode_label(mode)
        text = f"✅ {label}" if mode == current_mode else label
        builder.button(text=text, callback_data=f"set_render:{mode}")

    builder.button(text="🔙 Назад", callback_data="settings:main")
    builder.adjust(1)
    return builder.as_markup()


def get_approval_modes_keyboard(current_mode: str) -> InlineKeyboardMarkup:
    """Клавиатура выбора режима --approval-mode."""
    builder = InlineKeyboardBuilder()

    for mode in APPROVAL_MODE_LABELS:
        label = get_approval_mode_label(mode)
        text = f"✅ {label}" if mode == current_mode else label
        builder.button(text=text, callback_data=f"set_approval:{mode}")

    builder.button(text="🔙 Назад", callback_data="settings:main")
    builder.adjust(1)
    return builder.as_markup()


def get_interactive_approval_keyboard() -> InlineKeyboardMarkup:
    """Кнопки для подтверждения действия Gemini CLI."""
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Одобрить", callback_data="approve:yes")
    builder.button(text="❌ Отклонить", callback_data="approve:no")
    builder.button(text="⏭ Одобрять всё (YOLO)", callback_data="approve:yolo")
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
            text=f"{status} {name}", callback_data=f"mcp_toggle:{name}:{action}"
        )

    builder.adjust(1)
    # Добавляем кнопку обновления
    builder.row(
        InlineKeyboardButton(text="🔄 Обновить список", callback_data="mcp_refresh")
    )
    return builder.as_markup()


def get_skills_list_keyboard(skills: list[tuple[str, bool]]) -> InlineKeyboardMarkup:
    """Клавиатура со списком Skills и кнопками для их Вкл/Выкл."""
    builder = InlineKeyboardBuilder()

    for name, is_enabled in skills:
        status = "🟢" if is_enabled else "🔴"
        action = "disable" if is_enabled else "enable"
        # callback_data: "skill_toggle:<name>:<action>"
        builder.button(
            text=f"{status} {name}", callback_data=f"skill_toggle:{name}:{action}"
        )

    builder.adjust(1)
    # Добавляем кнопку обновления
    builder.row(
        InlineKeyboardButton(text="🔄 Обновить список", callback_data="skill_refresh")
    )
    return builder.as_markup()
