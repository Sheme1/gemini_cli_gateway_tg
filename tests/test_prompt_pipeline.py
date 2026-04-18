import asyncio
import shutil
from types import SimpleNamespace
import uuid
from pathlib import Path

import pytest

from gateway.bot.handlers.messages import process_gemini_prompt
from gateway.config import Config
from gateway.gemini.parser import StreamEvent


def make_test_dir() -> Path:
    root = Path.cwd() / ".test_runtime"
    root.mkdir(exist_ok=True)
    path = root / f"prompt-pipeline-{uuid.uuid4().hex}"
    path.mkdir()
    return path


class _FakeBot:
    def __init__(self) -> None:
        self._next_message_id = 1
        self.messages: list[dict] = []
        self.documents: list[dict] = []

    async def send_message(self, chat_id: int, text: str, reply_markup=None):
        message = {
            "chat_id": chat_id,
            "text": text,
            "reply_markup": reply_markup,
            "message_id": self._next_message_id,
        }
        self._next_message_id += 1
        self.messages.append(message)
        return SimpleNamespace(message_id=message["message_id"])

    async def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        reply_markup=None,
    ) -> None:
        self.messages.append(
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
                "reply_markup": reply_markup,
                "edited": True,
            }
        )

    async def send_document(self, chat_id: int, document, caption: str | None = None):
        filename = getattr(document, "filename", None)
        self.documents.append(
            {"chat_id": chat_id, "filename": filename, "caption": caption}
        )
        return SimpleNamespace(document=filename)


class _FakeUserSettings:
    def get_render_mode(self, _user_id: int) -> str:
        return "compact"


class _SoftFinalizeSessionManager:
    def __init__(self, artifact_path):
        self.artifact_path = artifact_path
        self.cancel_event = asyncio.Event()
        self.cancel_calls: list[str] = []

    async def send_prompt(self, prompt, user_id, on_event, on_approval) -> None:
        del prompt, user_id, on_approval
        await on_event(
            StreamEvent(
                event_type="tool_use",
                tool_name="write_file",
                tool_id="tool-1",
            )
        )
        self.artifact_path.write_text("docx", encoding="utf-8")
        await self.cancel_event.wait()

    async def cancel_active_prompt(self, user_id: int, reason: str = "") -> bool:
        self.cancel_calls.append(f"{user_id}:{reason}")
        self.cancel_event.set()
        return True


class _CompletingSessionManager:
    def __init__(self, artifact_path):
        self.artifact_path = artifact_path
        self.cancel_calls: list[str] = []

    async def send_prompt(self, prompt, user_id, on_event, on_approval) -> None:
        del prompt, user_id, on_approval
        await on_event(
            StreamEvent(
                event_type="tool_use",
                tool_name="write_file",
                tool_id="tool-1",
            )
        )
        self.artifact_path.write_text("docx", encoding="utf-8")
        await asyncio.sleep(0.08)
        await on_event(
            StreamEvent(
                event_type="assistant_text",
                assistant_text="Готово.",
            )
        )
        await on_event(
            StreamEvent(
                event_type="result_stats",
                total_tokens=12,
                duration_ms=400,
                is_done=True,
            )
        )

    async def cancel_active_prompt(self, user_id: int, reason: str = "") -> bool:
        self.cancel_calls.append(f"{user_id}:{reason}")
        return False


@pytest.mark.asyncio
async def test_process_prompt_soft_finalizes_after_artifact_delivery() -> None:
    tmp_path = make_test_dir()
    try:
        bot = _FakeBot()
        artifact = tmp_path / "referat.docx"
        session_manager = _SoftFinalizeSessionManager(artifact)
        config = Config(
            telegram_bot_token="token",
            gemini_working_dir=str(tmp_path),
            gemini_artifact_roots=(str(tmp_path),),
            stream_update_interval=0.01,
            artifact_watch_interval=0.02,
            artifact_stable_seconds=0.05,
            gemini_soft_finalize_idle_seconds=0.1,
        )

        await process_gemini_prompt(
            bot=bot,
            chat_id=1,
            user_id=42,
            prompt="test",
            session_manager=session_manager,
            config=config,
            user_settings=_FakeUserSettings(),
        )

        assert [doc["filename"] for doc in bot.documents] == ["referat.docx"]
        assert any("не прислал финальный result" in msg["text"] for msg in bot.messages)
        assert session_manager.cancel_calls
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


@pytest.mark.asyncio
async def test_process_prompt_sends_file_once_before_normal_completion() -> None:
    tmp_path = make_test_dir()
    try:
        bot = _FakeBot()
        artifact = tmp_path / "referat.docx"
        sidecar = tmp_path / "referat.md"
        lockfile = tmp_path / "package-lock.json"
        session_manager = _CompletingSessionManager(artifact)
        config = Config(
            telegram_bot_token="token",
            gemini_working_dir=str(tmp_path),
            gemini_artifact_roots=(str(tmp_path),),
            stream_update_interval=0.01,
            artifact_watch_interval=0.02,
            artifact_stable_seconds=0.01,
            gemini_soft_finalize_idle_seconds=1,
        )

        sidecar.write_text("markdown", encoding="utf-8")
        lockfile.write_text("{}", encoding="utf-8")

        await process_gemini_prompt(
            bot=bot,
            chat_id=1,
            user_id=42,
            prompt="test",
            session_manager=session_manager,
            config=config,
            user_settings=_FakeUserSettings(),
        )

        assert [doc["filename"] for doc in bot.documents] == ["referat.docx"]
        assert not session_manager.cancel_calls
        assert any("Готово." in msg["text"] for msg in bot.messages)
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)
