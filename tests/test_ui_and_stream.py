import asyncio
from pathlib import Path
import shutil
import uuid

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter

from gateway.bot.ui import build_settings_text
from gateway.config import Config
from gateway.streaming.editor import StreamEditor
from gateway.user_settings import UserSettingsStore
from tests.fake_telegram import FakeTelegramBot as _FakeBot
from tests.fake_telegram import HtmlRejectingFakeTelegramBot as _HtmlRejectingBot


class _SlowEditBot(_FakeBot):
    def __init__(self, delayed_text: str) -> None:
        super().__init__()
        self.delayed_text = delayed_text
        self.entered_delayed_edit = asyncio.Event()
        self.release_delayed_edit = asyncio.Event()
        self._delay_used = False

    async def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        parse_mode=None,
    ) -> None:
        await super().edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            parse_mode=parse_mode,
        )
        if text == self.delayed_text and not self._delay_used:
            self._delay_used = True
            self.entered_delayed_edit.set()
            await self.release_delayed_edit.wait()


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
    text = "Первая строка.\nВторая строка.\nТретья строка."

    chunk, remainder = editor._split_text(text, 25)

    assert chunk + remainder == text
    assert chunk.startswith("Первая строка")
    assert remainder.startswith("Вторая")


def test_stream_editor_splits_text_without_losing_spaces() -> None:
    editor = StreamEditor(bot=None, chat_id=1, max_length=30)  # type: ignore[arg-type]
    text = "Я обновляю структуру ваших личных инструкций."

    chunk, remainder = editor._split_text(text, 20)

    assert chunk + remainder == text
    assert "структуру ваших" in chunk + remainder


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
async def test_stream_editor_keeps_chunk_added_during_slow_edit() -> None:
    bot = _SlowEditBot(delayed_text="AB")
    editor = StreamEditor(bot=bot, chat_id=1, interval=0, min_update_chars=1)

    await editor.initialize("loading")
    await editor.append_text("A")
    await editor.append_text("B")
    await asyncio.wait_for(bot.entered_delayed_edit.wait(), timeout=1)

    append_task = asyncio.create_task(editor.append_text("C"))
    await asyncio.sleep(0)
    bot.release_delayed_edit.set()
    await asyncio.wait_for(append_task, timeout=1)
    await editor.flush()

    assert bot.edits[-1]["text"] == "ABC"
    assert editor.last_sent_text == "ABC"
    assert editor.text_buffer == ""


@pytest.mark.asyncio
async def test_stream_editor_keeps_long_answer_text_across_splits() -> None:
    bot = _FakeBot()
    editor = StreamEditor(
        bot=bot,
        chat_id=1,
        interval=0,
        max_length=80,
        min_update_chars=10,
    )
    text = "ДлинныйОтвет" * 80

    await editor.initialize("loading")
    for index in range(0, len(text), 37):
        await editor.append_text(text[index : index + 37])
    await editor.flush()

    stored_text = "".join(
        editor._message_texts[message_id] for message_id in editor._message_order
    )

    assert stored_text == text
    assert all(
        len(editor._message_texts[message_id]) <= 80
        for message_id in editor._message_order
    )


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


@pytest.mark.asyncio
async def test_stream_editor_finalizes_markdown_as_safe_html() -> None:
    bot = _FakeBot()
    editor = StreamEditor(bot=bot, chat_id=1, interval=0)

    await editor.initialize("loading")
    await editor.append_text("**Важно** <tag>\n- пункт")
    await editor.flush()

    assert bot.edits[-1]["parse_mode"] == "HTML"
    assert "<b>Важно</b> &lt;tag&gt;" in bot.edits[-1]["text"]
    assert "• пункт" in bot.edits[-1]["text"]


@pytest.mark.asyncio
async def test_stream_editor_falls_back_to_plain_text_after_html_error() -> None:
    bot = _HtmlRejectingBot()
    editor = StreamEditor(bot=bot, chat_id=1, interval=0)

    await editor.initialize("loading")
    await editor.append_text("**Важно** <tag>")
    await editor.flush()

    assert any(edit["parse_mode"] == "HTML" for edit in bot.edits)
    assert bot.edits[-1]["parse_mode"] is None
    assert bot.edits[-1]["text"] == "**Важно** <tag>"


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
