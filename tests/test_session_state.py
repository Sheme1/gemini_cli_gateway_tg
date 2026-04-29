import json
from pathlib import Path
import shutil
from types import SimpleNamespace
import uuid

import pytest

from gateway.bot.handlers import callbacks, commands
from gateway.config import Config
from gateway.gemini.session import SessionManager
from gateway.session_state import SessionStateStore


def make_test_dir() -> Path:
    root = Path.cwd() / ".test_runtime"
    root.mkdir(exist_ok=True)
    path = root / f"session-state-{uuid.uuid4().hex}"
    path.mkdir()
    return path


class _Message:
    def __init__(self) -> None:
        self.from_user = SimpleNamespace(id=42, full_name="Test User")
        self.answers: list[str] = []

    async def answer(self, text: str, **_kwargs):
        self.answers.append(text)


class _CallbackMessage:
    def __init__(self) -> None:
        self.edits: list[str] = []
        self.answers: list[str] = []
        self.text = "message"

    async def edit_text(self, text: str, reply_markup=None):
        del reply_markup
        self.edits.append(text)

    async def answer(self, text: str, **_kwargs):
        self.answers.append(text)


class _Callback:
    def __init__(self, data: str) -> None:
        self.data = data
        self.from_user = SimpleNamespace(id=42)
        self.message = _CallbackMessage()
        self.answers: list[str | None] = []

    async def answer(self, text: str | None = None, show_alert=None):
        del show_alert
        self.answers.append(text)


class _UserSettings:
    def __init__(self) -> None:
        self.preset = "auto"

    def get_model_preset(self, _user_id: int) -> str:
        return self.preset

    def set_model_preset(self, _user_id: int, model_preset: str) -> str:
        self.preset = model_preset
        return model_preset

    def get_effective_model(self, _user_id: int, _fallback_model: str) -> str:
        return self.preset


class _ListProcess:
    returncode = 0

    def __init__(self, output: str) -> None:
        self.output = output

    async def communicate(self):
        return self.output.encode("utf-8"), b""


class _Stream:
    def __init__(self, lines: list[str]) -> None:
        self.lines = [f"{line}\n".encode("utf-8") for line in lines]

    async def readline(self) -> bytes:
        if self.lines:
            return self.lines.pop(0)
        return b""


class _PromptProcess:
    def __init__(self, lines: list[str]) -> None:
        self.stdout = _Stream(lines)
        self.stderr = _Stream([])
        self.returncode = None

    async def wait(self) -> int:
        self.returncode = 0
        return 0

    def terminate(self) -> None:
        self.returncode = -15

    def kill(self) -> None:
        self.returncode = -9


def _make_session_state_config(tmp_path: Path) -> Config:
    return Config(
        telegram_bot_token="token",
        gemini_working_dir=str(tmp_path),
        gemini_artifact_roots=(str(tmp_path),),
        gateway_state_dir=str(tmp_path / "state"),
    )


def _stub_session_processes(
    monkeypatch,
    *,
    list_output: str,
    prompt_session_id: str,
) -> list[tuple[tuple[str, ...], dict]]:
    calls: list[tuple[tuple[str, ...], dict]] = []

    async def fake_create_subprocess_exec(*args, **kwargs):
        calls.append((args, kwargs))
        if "--list-sessions" in args:
            return _ListProcess(list_output)
        return _PromptProcess(
            [
                json.dumps({"type": "init", "session_id": prompt_session_id}),
                json.dumps({"type": "result", "status": "success"}),
            ]
        )

    monkeypatch.setattr(
        "gateway.gemini.session.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )
    return calls


async def _noop_event(_event):
    return None


async def _noop_approval(_req):
    return None


def test_session_state_store_persists_active_session() -> None:
    tmp_path = make_test_dir()
    try:
        path = tmp_path / "session_state.json"
        store = SessionStateStore(path)

        store.set(
            42,
            active_session_id="session-1",
            workspace="/workspace",
            source="captured",
        )

        reloaded = SessionStateStore(path)
        record = reloaded.get(42, workspace="/workspace")

        assert record is not None
        assert record.active_session_id == "session-1"
        assert record.source == "captured"
        assert reloaded.get(42, workspace="/other") is None
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_session_state_store_ignores_broken_json() -> None:
    tmp_path = make_test_dir()
    try:
        path = tmp_path / "session_state.json"
        path.write_text("{broken", encoding="utf-8")

        store = SessionStateStore(path)

        assert store.get(42, workspace="/workspace") is None
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_session_state_store_marks_explicit_reset() -> None:
    tmp_dir = make_test_dir()
    try:
        path = tmp_dir / "session_state.json"
        store = SessionStateStore(path)

        store.mark_cleared(42, workspace="/workspace", source="/new")

        assert store.get(42, workspace="/workspace") is None
        assert store.is_cleared(42, workspace="/workspace") is True
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_session_manager_resumes_persisted_session_after_restart(
    monkeypatch,
) -> None:
    tmp_path = make_test_dir()
    session_id = "11111111-1111-4111-8111-111111111111"
    calls = _stub_session_processes(
        monkeypatch,
        list_output=f"1. Saved (Just now) [{session_id}]\n",
        prompt_session_id=session_id,
    )

    try:
        config = _make_session_state_config(tmp_path)
        first_manager = SessionManager(config)
        await first_manager.set_active_session(42, session_id)

        restarted_manager = SessionManager(config)

        await restarted_manager.send_prompt("hello", 42, _noop_event, _noop_approval)

        prompt_args = calls[-1][0]
        assert prompt_args[-2:] == ("--resume", session_id)
        assert restarted_manager.get_active_session(42) == session_id
        assert restarted_manager.get_active_session_source(42) == "persisted"
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


@pytest.mark.asyncio
async def test_session_manager_new_reset_disables_latest_fallback(
    monkeypatch,
) -> None:
    tmp_path = make_test_dir()
    old_session_id = "33333333-3333-4333-8333-333333333333"
    new_session_id = "44444444-4444-4444-8444-444444444444"
    calls = _stub_session_processes(
        monkeypatch,
        list_output=f"1. Old (Just now) [{old_session_id}]\n",
        prompt_session_id=new_session_id,
    )

    try:
        config = _make_session_state_config(tmp_path)
        manager = SessionManager(config)
        await manager.reset(42, reason="/new")

        await manager.send_prompt("hello", 42, _noop_event, _noop_approval)

        prompt_args = calls[-1][0]
        assert "--resume" not in prompt_args
        assert manager.get_active_session(42) == new_session_id
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


@pytest.mark.asyncio
async def test_session_manager_auto_resumes_latest_when_state_is_empty(
    monkeypatch,
) -> None:
    tmp_path = make_test_dir()
    session_id = "22222222-2222-4222-8222-222222222222"
    calls = _stub_session_processes(
        monkeypatch,
        list_output=f"1. Existing (Just now) [{session_id}]\n",
        prompt_session_id=session_id,
    )

    try:
        config = _make_session_state_config(tmp_path)
        manager = SessionManager(config)

        await manager.send_prompt("hello", 42, _noop_event, _noop_approval)

        prompt_args = calls[-1][0]
        assert prompt_args[-2:] == ("--resume", "latest")
        assert manager.get_active_session(42) == session_id
        assert manager.get_active_session_source(42) == "latest-fallback"
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


@pytest.mark.asyncio
async def test_session_manager_can_run_transient_prompt_without_resume(
    monkeypatch,
) -> None:
    tmp_path = make_test_dir()
    old_session_id = "22222222-2222-4222-8222-222222222222"
    prompt_session_id = "55555555-5555-4555-8555-555555555555"
    calls = _stub_session_processes(
        monkeypatch,
        list_output=f"1. Existing (Just now) [{old_session_id}]\n",
        prompt_session_id=prompt_session_id,
    )

    try:
        config = _make_session_state_config(tmp_path)
        manager = SessionManager(config)

        await manager.send_prompt(
            "hello",
            42,
            _noop_event,
            _noop_approval,
            resume_session=False,
            persist_session=False,
        )

        prompt_args = calls[-1][0]
        assert "--resume" not in prompt_args
        assert manager.get_active_session(42) is None
        assert manager.session_state.is_cleared(
            42,
            workspace=manager.working_dir_for_user(42),
        )
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


@pytest.mark.asyncio
async def test_internal_generate_text_uses_service_working_dir(monkeypatch):
    tmp_path = make_test_dir()
    captured_kwargs = {}

    async def fake_create_subprocess_exec(*_args, **kwargs):
        captured_kwargs.update(kwargs)
        return _PromptProcess(
            [
                json.dumps(
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": "# Личные инструкции\n\n- One\n- Two\n- Three\n- Four\n- Five",
                    }
                ),
                json.dumps({"type": "result", "status": "success"}),
            ]
        )

    monkeypatch.setattr(
        "gateway.gemini.session.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    try:
        config = Config(
            telegram_bot_token="token",
            gemini_working_dir=str(tmp_path / "workspace"),
            gemini_artifact_roots=(str(tmp_path / "workspace"),),
            gateway_state_dir=str(tmp_path / "state"),
        )
        manager = SessionManager(config)

        await manager.generate_text("build init", user_id=42)

        assert captured_kwargs["cwd"] == str(
            tmp_path / "state" / "internal" / "tg-user-42" / "init"
        )
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


@pytest.mark.asyncio
async def test_start_command_does_not_reset_context() -> None:
    class _SessionManager:
        def get_active_session(self, _user_id: int) -> str:
            return "session-1"

    message = _Message()

    await commands.command_start_handler(message, _SessionManager())  # type: ignore[arg-type]

    assert "session-1" in message.answers[-1]


@pytest.mark.asyncio
async def test_model_callback_does_not_reset_context() -> None:
    callback = _Callback("model:flash")
    config = Config(telegram_bot_token="token")
    user_settings = _UserSettings()

    await callbacks.callback_model(callback, config, user_settings)  # type: ignore[arg-type]

    assert "сохранён" in callback.message.edits[-1]
