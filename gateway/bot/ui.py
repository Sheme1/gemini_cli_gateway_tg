from __future__ import annotations

from gateway.config import Config
from gateway.user_settings import DEFAULT_RENDER_MODE

RENDER_MODE_LABELS = {
    "compact": "Компактный",
    "summary": "Сводка",
    "detailed": "Подробно",
}

RENDER_MODE_DESCRIPTIONS = {
    "compact": "Показывает только итоговый ответ и важные статусы.",
    "summary": "Показывает ответ и короткие статусы инструментов.",
    "detailed": "Показывает ответ и расширенные технические детали.",
}

APPROVAL_MODE_LABELS = {
    "default": "Спрашивать",
    "auto_edit": "Авто для правок",
    "yolo": "YOLO",
    "plan": "План",
}

APPROVAL_MODE_DESCRIPTIONS = {
    "default": "В headless CLI интерактивный ask_user останавливает запрос; для правил используйте Policy Engine.",
    "auto_edit": "Правки файлов разрешаются автоматически, остальное спрашивается.",
    "yolo": "Бот автоматически одобряет все действия.",
    "plan": "Только чтение и планирование без изменений.",
}


def get_render_mode_label(mode: str) -> str:
    return RENDER_MODE_LABELS.get(mode, RENDER_MODE_LABELS[DEFAULT_RENDER_MODE])


def get_render_mode_description(mode: str) -> str:
    return RENDER_MODE_DESCRIPTIONS.get(
        mode,
        RENDER_MODE_DESCRIPTIONS[DEFAULT_RENDER_MODE],
    )


def get_approval_mode_label(mode: str) -> str:
    return APPROVAL_MODE_LABELS.get(mode, mode)


def get_approval_mode_description(mode: str) -> str:
    return APPROVAL_MODE_DESCRIPTIONS.get(
        mode,
        "Определяет, когда бот просит подтверждение действий.",
    )


def get_sandbox_label(enabled: bool) -> str:
    return "Включена" if enabled else "Выключена"


def get_sandbox_description(_: bool) -> str:
    return "Ограничивает выполнение Gemini в sandbox-режиме."


def get_timeout_description(_: int) -> str:
    return "Сколько ждать без активности перед остановкой запроса."


def build_settings_text(config: Config, render_mode: str) -> str:
    policy_state = "включены" if config.gemini_policy_paths else "нет"
    admin_policy_state = "включены" if config.gemini_admin_policy_paths else "нет"
    extensions_state = (
        ", ".join(config.gemini_extensions) if config.gemini_extensions else "все"
    )
    mcp_allowlist_state = (
        ", ".join(config.gemini_allowed_mcp_server_names)
        if config.gemini_allowed_mcp_server_names
        else "без allowlist"
    )
    return (
        "⚙️ <b>Настройки Gemini CLI</b>\n\n"
        "<b>Вывод</b>\n"
        f"<b>Режим отображения:</b> {get_render_mode_label(render_mode)}\n"
        f"<i>Кратко:</i> {get_render_mode_description(render_mode)}\n\n"
        "<b>Безопасность</b>\n"
        f"<b>Режим подтверждений:</b> {get_approval_mode_label(config.gemini_approval_mode)}\n"
        f"<i>Кратко:</i> {get_approval_mode_description(config.gemini_approval_mode)}\n\n"
        f"<b>Песочница:</b> {get_sandbox_label(config.gemini_sandbox)}\n"
        f"<i>Кратко:</i> {get_sandbox_description(config.gemini_sandbox)}\n"
        f"<b>Trust bypass:</b> {'--skip-trust' if config.gemini_skip_trust else 'env/interactive trust'}\n"
        f"<b>Policy:</b> {policy_state}; admin: {admin_policy_state}\n\n"
        "<b>Модель</b>\n"
        f"<b>Модель из .env:</b> <code>{config.gemini_model}</code>\n\n"
        "<b>Runtime</b>\n"
        f"<b>Таймаут Gemini CLI:</b> {config.gemini_cli_timeout} сек\n"
        f"<i>Кратко:</i> {get_timeout_description(config.gemini_cli_timeout)}\n"
        f"<b>Extensions:</b> <code>{extensions_state}</code>\n"
        f"<b>MCP allowlist:</b> <code>{mcp_allowlist_state}</code>\n"
        f"<b>Screen reader:</b> {'включён' if config.gemini_screen_reader else 'выключен'}"
    )
