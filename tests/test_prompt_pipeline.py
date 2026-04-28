import asyncio
import shutil
import uuid
from pathlib import Path

import pytest

from gateway.bot.handlers.messages import process_gemini_prompt
from gateway.config import Config
from gateway.gemini.parser import StreamEvent
from gateway.prompt_guard import PendingPromptStore
from gateway.usage import UsageLedger
from tests.fake_telegram import ReplyMarkupFakeTelegramBot as _FakeBot


def make_test_dir() -> Path:
    root = Path.cwd() / ".test_runtime"
    root.mkdir(exist_ok=True)
    path = root / f"prompt-pipeline-{uuid.uuid4().hex}"
    path.mkdir()
    return path


class _FakeUserSettings:
    def get_render_mode(self, _user_id: int) -> str:
        return "compact"


class _ArtifactSessionManagerBase:
    def __init__(self, artifact_path):
        self.artifact_path = artifact_path
        self.cancel_calls: list[str] = []

    def has_active_prompt(self, _user_id: int) -> bool:
        return False

    async def _emit_artifact_write(self, on_event) -> None:
        await on_event(
            StreamEvent(
                event_type="tool_use",
                tool_name="write_file",
                tool_id="tool-1",
            )
        )
        self.artifact_path.write_text("docx", encoding="utf-8")

    def _record_cancel(self, user_id: int, reason: str) -> None:
        self.cancel_calls.append(f"{user_id}:{reason}")


class _SoftFinalizeSessionManager(_ArtifactSessionManagerBase):
    def __init__(self, artifact_path):
        super().__init__(artifact_path)
        self.cancel_event = asyncio.Event()

    async def send_prompt(self, prompt, user_id, on_event, on_approval) -> None:
        del prompt, user_id, on_approval
        await self._emit_artifact_write(on_event)
        await self.cancel_event.wait()

    async def cancel_active_prompt(self, user_id: int, reason: str = "") -> bool:
        self._record_cancel(user_id, reason)
        self.cancel_event.set()
        return True


class _CompletingSessionManager(_ArtifactSessionManagerBase):
    async def send_prompt(self, prompt, user_id, on_event, on_approval) -> None:
        del prompt, user_id, on_approval
        await self._emit_artifact_write(on_event)
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
        self._record_cancel(user_id, reason)
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


@pytest.mark.asyncio
async def test_process_prompt_warns_before_large_prompt() -> None:
    tmp_path = make_test_dir()
    try:
        bot = _FakeBot()
        config = Config(
            telegram_bot_token="token",
            gemini_working_dir=str(tmp_path),
            gemini_artifact_roots=(str(tmp_path),),
            prompt_warn_chars=5,
            prompt_max_chars=100,
        )

        await process_gemini_prompt(
            bot=bot,
            chat_id=1,
            user_id=42,
            prompt="long prompt",
            session_manager=_CompletingSessionManager(tmp_path / "x.txt"),
            config=config,
            user_settings=_FakeUserSettings(),
            prompt_guard=PendingPromptStore(),
        )

        assert "Запрос большой" in bot.messages[-1]["text"]
        assert bot.messages[-1]["reply_markup"] is not None
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


@pytest.mark.asyncio
async def test_process_prompt_blocks_prompt_over_hard_limit() -> None:
    tmp_path = make_test_dir()
    try:
        bot = _FakeBot()
        config = Config(
            telegram_bot_token="token",
            gemini_working_dir=str(tmp_path),
            gemini_artifact_roots=(str(tmp_path),),
            prompt_warn_chars=5,
            prompt_max_chars=8,
        )

        await process_gemini_prompt(
            bot=bot,
            chat_id=1,
            user_id=42,
            prompt="too long prompt",
            session_manager=_CompletingSessionManager(tmp_path / "x.txt"),
            config=config,
            user_settings=_FakeUserSettings(),
            prompt_guard=PendingPromptStore(),
        )

        assert "слишком большой" in bot.messages[-1]["text"]
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


@pytest.mark.asyncio
async def test_process_prompt_records_usage_tokens() -> None:
    tmp_path = make_test_dir()
    try:
        bot = _FakeBot()
        artifact = tmp_path / "referat.docx"
        config = Config(
            telegram_bot_token="token",
            gemini_working_dir=str(tmp_path),
            gemini_artifact_roots=(str(tmp_path),),
            stream_update_interval=0.01,
            artifact_watch_interval=0.02,
            artifact_stable_seconds=0.01,
        )
        usage_ledger = UsageLedger(tmp_path / "state")

        await process_gemini_prompt(
            bot=bot,
            chat_id=1,
            user_id=42,
            prompt="test",
            session_manager=_CompletingSessionManager(artifact),
            config=config,
            user_settings=_FakeUserSettings(),
            usage_ledger=usage_ledger,
        )

        snapshot = usage_ledger.snapshot(42)
        assert snapshot.user_tokens == 12
        assert snapshot.global_tokens == 12
        assert snapshot.last_request["model"] == config.gemini_model
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)
