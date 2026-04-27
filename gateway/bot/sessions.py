from __future__ import annotations

from html import escape
from math import ceil

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from gateway.gemini.session import GeminiSessionInfo

SESSIONS_PAGE_SIZE = 5


def build_sessions_page(
    sessions: list[GeminiSessionInfo],
    page: int = 0,
) -> tuple[str, InlineKeyboardMarkup]:
    total_pages = max(1, ceil(len(sessions) / SESSIONS_PAGE_SIZE))
    safe_page = min(max(page, 0), total_pages - 1)
    start = safe_page * SESSIONS_PAGE_SIZE
    page_sessions = sessions[start : start + SESSIONS_PAGE_SIZE]

    text_lines = [
        "📂 <b>Доступные диалоги</b>",
        f"Страница {safe_page + 1}/{total_pages}. Новые сверху.",
        "Gemini CLI обычно чистит историю старше 30 дней, если настройки не изменены.",
        "",
    ]

    builder = InlineKeyboardBuilder()
    if safe_page == 0 and sessions:
        builder.row(
            InlineKeyboardButton(
                text="Открыть latest",
                callback_data="session:open-latest",
            )
        )

    for offset, session in enumerate(page_sessions, start=1):
        display_index = start + offset
        current = " · текущий" if session.is_current else ""
        text_lines.extend(
            [
                f"{display_index}. <b>{escape(_clip(session.title, 80))}</b>",
                f"   обновлён: {escape(session.relative_time)}{current}",
                f"   index: <code>{session.source_index}</code>",
                f"   id: <code>{escape(session.short_id)}</code>",
                f"   uuid: <code>{escape(session.session_id)}</code>",
                "",
            ]
        )
        builder.row(
            InlineKeyboardButton(
                text=f"Открыть #{display_index}",
                callback_data=f"session:open:{session.session_id}",
            ),
            InlineKeyboardButton(
                text="Удалить",
                callback_data=f"session:delete:{session.session_id}",
            ),
        )

    nav_buttons: list[InlineKeyboardButton] = []
    if safe_page > 0:
        nav_buttons.append(
            InlineKeyboardButton(
                text="◀ Назад",
                callback_data=f"session:page:{safe_page - 1}",
            )
        )
    if safe_page < total_pages - 1:
        nav_buttons.append(
            InlineKeyboardButton(
                text="Вперёд ▶",
                callback_data=f"session:page:{safe_page + 1}",
            )
        )
    if nav_buttons:
        builder.row(*nav_buttons)

    builder.row(
        InlineKeyboardButton(
            text="🔄 Обновить",
            callback_data=f"session:refresh:{safe_page}",
        ),
        InlineKeyboardButton(
            text="⬇️ Экспорт TXT",
            callback_data="session:export",
        ),
    )

    return "\n".join(text_lines).rstrip(), builder.as_markup()


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."
