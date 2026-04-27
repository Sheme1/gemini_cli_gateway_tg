from types import SimpleNamespace

import pytest

from gateway.bot.handlers import callbacks
from gateway.bot.sessions import build_sessions_page
from gateway.gemini.session import GeminiSessionInfo


def _session(index: int, title: str, time: str = "Just now") -> GeminiSessionInfo:
    return GeminiSessionInfo(
        session_id=f"{index:08d}-1111-4111-8111-111111111111",
        title=title,
        relative_time=time,
        is_current=False,
        source_index=index,
        sort_index=index,
    )


def test_build_sessions_page_shows_titles_and_navigation() -> None:
    sessions = [_session(index, f"Диалог {index}") for index in range(12, 0, -1)]

    text, markup = build_sessions_page(sessions, page=0)

    assert "Страница 1/3" in text
    assert "Диалог 12" in text
    assert "Диалог 7" not in text
    assert "00000012..." in text
    assert markup.inline_keyboard[-2][0].text == "Вперёд ▶"
    assert markup.inline_keyboard[-1][0].text == "🔄 Обновить"


def test_build_sessions_page_clamps_page_number() -> None:
    sessions = [_session(index, f"Диалог {index}") for index in range(2, 0, -1)]

    text, markup = build_sessions_page(sessions, page=99)

    assert "Страница 1/1" in text
    assert markup.inline_keyboard[-1][0].callback_data == "session:refresh:0"


class _FakeMessage:
    def __init__(self) -> None:
        self.edits: list[dict] = []

    async def edit_text(self, text: str, reply_markup=None):
        self.edits.append({"text": text, "reply_markup": reply_markup})


class _FakeCallback:
    def __init__(self, data: str) -> None:
        self.data = data
        self.from_user = SimpleNamespace(id=42)
        self.message = _FakeMessage()
        self.answers: list[str | None] = []

    async def answer(self, text: str | None = None, show_alert=None):
        del show_alert
        self.answers.append(text)


class _FakeSessionManager:
    def __init__(self) -> None:
        self.sessions = [
            _session(index, f"Диалог {index}") for index in range(8, 0, -1)
        ]
        self.active: tuple[int, str] | None = None

    async def get_sessions_list(self, *_args, **_kwargs):
        return self.sessions

    async def set_active_session(self, user_id: int, session_id: str) -> None:
        self.active = (user_id, session_id)


@pytest.mark.asyncio
async def test_sessions_page_callback_edits_existing_message() -> None:
    callback = _FakeCallback("session:page:1")
    session_manager = _FakeSessionManager()

    await callbacks.callback_sessions_page(callback, session_manager)  # type: ignore[arg-type]

    assert "Страница 2/2" in callback.message.edits[-1]["text"]
    assert callback.answers == [None]


@pytest.mark.asyncio
async def test_session_open_callback_sets_active_session() -> None:
    session_manager = _FakeSessionManager()
    session_id = session_manager.sessions[0].session_id
    callback = _FakeCallback(f"session:open:{session_id}")

    await callbacks.callback_resume_session(callback, session_manager)  # type: ignore[arg-type]

    assert session_manager.active == (42, session_id)
    assert "Диалог выбран" in callback.message.edits[-1]["text"]
