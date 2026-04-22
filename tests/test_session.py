import asyncio
import json

import pytest

from gateway.config import Config
from gateway.gemini.session import SessionManager


class _FakeStream:
    def __init__(self, lines: list[str], wait_event: asyncio.Event | None = None):
        self._lines = [f"{line}\n".encode("utf-8") for line in lines]
        self._wait_event = wait_event

    async def readline(self) -> bytes:
        if self._lines:
            return self._lines.pop(0)
        if self._wait_event:
            await self._wait_event.wait()
        return b""


class _FakeProcess:
    def __init__(
        self,
        lines: list[str],
        stderr_lines: list[str] | None = None,
        returncode_on_wait: int = 0,
        block_stdout: bool = False,
    ):
        self._finished = asyncio.Event()
        self.stdout = _FakeStream(lines, self._finished if block_stdout else None)
        self.stderr = _FakeStream(stderr_lines or [])
        self._returncode_on_wait = returncode_on_wait
        self.returncode = None

    def terminate(self) -> None:
        self.returncode = -15
        self._finished.set()

    def kill(self) -> None:
        self.returncode = -9
        self._finished.set()

    async def wait(self) -> int:
        if self.returncode is None:
            self.returncode = self._returncode_on_wait
            self._finished.set()
        return self.returncode


@pytest.mark.asyncio
async def test_session_manager_maps_tool_result_to_tool_name(monkeypatch) -> None:
    lines = [
        json.dumps(
            {
                "type": "init",
                "session_id": "session-1",
                "model": "gemini-3-flash-preview",
            }
        ),
        json.dumps(
            {
                "type": "tool_use",
                "tool_name": "write_file",
                "tool_id": "tool-1",
                "parameters": {"path": "./draft.md"},
            }
        ),
        json.dumps(
            {
                "type": "tool_result",
                "tool_id": "tool-1",
                "status": "success",
                "output": "saved",
            }
        ),
        json.dumps(
            {
                "type": "result",
                "status": "success",
                "stats": {"total_tokens": 42, "duration_ms": 1200},
            }
        ),
    ]

    async def fake_create_subprocess_exec(*_args, **_kwargs):
        return _FakeProcess(lines)

    monkeypatch.setattr(
        "gateway.gemini.session.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    config = Config(
        telegram_bot_token="token",
        gemini_working_dir=".",
        gemini_artifact_roots=(".",),
    )
    manager = SessionManager(config)
    events = []

    async def on_event(event):
        events.append(event)

    async def on_approval(_req):
        raise AssertionError("approval request was not expected")

    await manager.send_prompt(
        prompt="test",
        user_id=123,
        on_event=on_event,
        on_approval=on_approval,
    )

    assert [event.event_type for event in events] == [
        "tool_use",
        "tool_result",
        "result_stats",
    ]
    assert events[1].tool_name == "write_file"


@pytest.mark.asyncio
async def test_session_manager_can_cancel_active_prompt(monkeypatch) -> None:
    process_holder = {}

    async def fake_create_subprocess_exec(*_args, **_kwargs):
        process = _FakeProcess([], block_stdout=True)
        process_holder["process"] = process
        return process

    monkeypatch.setattr(
        "gateway.gemini.session.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    config = Config(
        telegram_bot_token="token",
        gemini_working_dir=".",
        gemini_artifact_roots=(".",),
    )
    manager = SessionManager(config)
    events = []

    async def on_event(event):
        events.append(event)

    async def on_approval(_req):
        return None

    task = asyncio.create_task(
        manager.send_prompt(
            prompt="test",
            user_id=321,
            on_event=on_event,
            on_approval=on_approval,
        )
    )
    await asyncio.sleep(0)

    cancelled = await manager.cancel_active_prompt(321, reason="test")
    await task

    assert cancelled is True
    assert process_holder["process"].returncode == -15
    assert not [event for event in events if event.event_type == "error"]


@pytest.mark.asyncio
async def test_session_manager_emits_stderr_on_nonzero_exit(monkeypatch) -> None:
    async def fake_create_subprocess_exec(*_args, **_kwargs):
        return _FakeProcess([], stderr_lines=["auth failed"], returncode_on_wait=1)

    monkeypatch.setattr(
        "gateway.gemini.session.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    config = Config(
        telegram_bot_token="token",
        gemini_working_dir=".",
        gemini_artifact_roots=(".",),
    )
    manager = SessionManager(config)
    events = []

    async def on_event(event):
        events.append(event)

    async def on_approval(_req):
        raise AssertionError("approval request was not expected")

    await manager.send_prompt(
        prompt="test",
        user_id=123,
        on_event=on_event,
        on_approval=on_approval,
    )

    assert events[-1].event_type == "error"
    assert "auth failed" in events[-1].error_message


@pytest.mark.asyncio
async def test_session_manager_deduplicates_full_message_snapshots(monkeypatch) -> None:
    lines = [
        json.dumps({"type": "message", "role": "assistant", "content": "Привет"}),
        json.dumps({"type": "message", "role": "assistant", "content": "Привет мир"}),
        json.dumps(
            {
                "type": "result",
                "status": "success",
                "stats": {"total_tokens": 12, "duration_ms": 100},
            }
        ),
    ]

    async def fake_create_subprocess_exec(*_args, **_kwargs):
        return _FakeProcess(lines)

    monkeypatch.setattr(
        "gateway.gemini.session.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    config = Config(
        telegram_bot_token="token",
        gemini_working_dir=".",
        gemini_artifact_roots=(".",),
    )
    manager = SessionManager(config)
    chunks = []

    async def on_event(event):
        if event.event_type == "assistant_text":
            chunks.append(event.assistant_text)

    async def on_approval(_req):
        raise AssertionError("approval request was not expected")

    await manager.send_prompt(
        prompt="test",
        user_id=123,
        on_event=on_event,
        on_approval=on_approval,
    )

    assert chunks == ["Привет", " мир"]
