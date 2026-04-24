import asyncio
from pathlib import Path
import shutil
from types import SimpleNamespace
import uuid

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter

from gateway.bot.ui import build_settings_text
from gateway.config import Config
from gateway.streaming.editor import StreamEditor
from gateway.user_settings import UserSettingsStore


def make_test_dir() -> Path:
    root = Path.cwd() / ".test_runtime"
    root.mkdir(exist_ok=True)
    path = root / f"settings-{uuid.uuid4().hex}"
    path.mkdir()
    return path


def test_settings_text_is_fully_russian() -> None:
    config = Config(
        telegram_bot_token="token",
        gemini_working_dir=".",
        gemini_artifact_roots=(".",),
    )

    text = build_settings_text(config, "compact")

    assert "Режим отображения" in text
    assert "Кратко" in text
    assert "Песочница" in text
    assert "Sandbox" not in text
    assert "approval" not in text


def test_stream_editor_splits_text_without_breaking_beginning() -> None:
    editor = StreamEditor(bot=None, chat_id=1, max_length=40)  # type: ignore[arg-type]

    chunk, remainder = editor._split_text(
        "Первая строка.\nВторая строка.\nТретья строка.",
        25,
    )

    assert chunk.startswith("Первая строка")
    assert remainder.startswith("Вторая")


class _FakeBot:
    def __init__(self, failures=None) -> None:
        self._next_message_id = 1
        self.failures = list(failures or [])
        self.sent: list[dict] = []
        self.edits: list[dict] = []

    async def send_message(self, chat_id: int, text: str, parse_mode=None):
        message = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "message_id": self._next_message_id,
        }
        self._next_message_id += 1
        self.sent.append(message)
        return SimpleNamespace(message_id=message["message_id"])

    async def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        parse_mode=None,
    ) -> None:
        self.edits.append(
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
                "parse_mode": parse_mode,
            }
        )
        if self.failures:
            raise self.failures.pop(0)


@pytest.mark.asyncio
async def test_stream_editor_does_not_keep_status_in_final_answer() -> None:
    bot = _FakeBot()
    editor = StreamEditor(bot=bot, chat_id=1, interval=0)

    await editor.initialize("loading")
    await editor.append_text("Ответ")
    await editor.set_status("⏳ Выполняю шаг")
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert bot.edits[-1]["text"] == "Ответ\n\n⏳ Выполняю шаг"

    await editor.set_status("")
    await editor.flush()

    assert bot.edits[-1]["text"] == "Ответ"


@pytest.mark.asyncio
async def test_stream_editor_edits_first_answer_chunk_immediately() -> None:
    bot = _FakeBot()
    editor = StreamEditor(bot=bot, chat_id=1, interval=60, min_update_chars=100)

    await editor.initialize("loading")
    await editor.append_text("Первый чанк")

    assert bot.edits[-1]["text"] == "Первый чанк"


@pytest.mark.asyncio
async def test_stream_editor_coalesces_later_small_chunks() -> None:
    bot = _FakeBot()
    editor = StreamEditor(bot=bot, chat_id=1, interval=60, min_update_chars=100)

    await editor.initialize("loading")
    await editor.append_text("Первый")
    edit_count = len(bot.edits)
    await editor.append_text(" маленький")
    await asyncio.sleep(0)

    assert len(bot.edits) == edit_count

    await editor.flush()

    assert bot.edits[-1]["text"] == "Первый маленький"


@pytest.mark.asyncio
async def test_stream_editor_falls_back_to_new_message_when_edit_is_gone() -> None:
    bot = _FakeBot(
        failures=[
            TelegramBadRequest(
                method=None,  # type: ignore[arg-type]
                message="Bad Request: message to edit not found",
            )
        ]
    )
    editor = StreamEditor(bot=bot, chat_id=1, interval=0)

    await editor.initialize("loading")
    await editor.append_text("Ответ")
    await editor.flush()

    assert bot.sent[-1]["text"] == "Ответ"
    assert editor.current_message_id == bot.sent[-1]["message_id"]


@pytest.mark.asyncio
async def test_stream_editor_retries_after_telegram_rate_limit() -> None:
    bot = _FakeBot(
        failures=[
            TelegramRetryAfter(
                method=None,  # type: ignore[arg-type]
                message="Too Many Requests",
                retry_after=0,
            )
        ]
    )
    editor = StreamEditor(bot=bot, chat_id=1, interval=0)

    await editor.initialize("loading")
    await editor.append_text("Ответ")
    await editor.flush()

    assert len(bot.edits) == 2
    assert bot.edits[-1]["text"] == "Ответ"


def test_user_settings_store_persists_render_mode() -> None:
    tmp_path = make_test_dir()
    try:
        store = UserSettingsStore(path=tmp_path / "user_settings.json")

        store.set_render_mode(42, "detailed")

        reloaded = UserSettingsStore(path=tmp_path / "user_settings.json")
        assert reloaded.get_render_mode(42) == "detailed"
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_user_settings_store_persists_model_preset() -> None:
    tmp_path = make_test_dir()
    try:
        store = UserSettingsStore(path=tmp_path / "user_settings.json")

        store.set_model_preset(42, "quality")

        reloaded = UserSettingsStore(path=tmp_path / "user_settings.json")
        assert reloaded.get_model_preset(42) == "quality"
        assert reloaded.get_effective_model(42, "fallback-model") == (
            "gemini-3.1-pro-preview"
        )

        reloaded.set_model_preset(42, "env")
        assert reloaded.get_effective_model(42, "fallback-model") == "fallback-model"
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)
