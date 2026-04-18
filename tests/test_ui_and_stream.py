from pathlib import Path
import shutil
import uuid

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


def test_user_settings_store_persists_render_mode() -> None:
    tmp_path = make_test_dir()
    try:
        store = UserSettingsStore(path=tmp_path / "user_settings.json")

        store.set_render_mode(42, "detailed")

        reloaded = UserSettingsStore(path=tmp_path / "user_settings.json")
        assert reloaded.get_render_mode(42) == "detailed"
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)
