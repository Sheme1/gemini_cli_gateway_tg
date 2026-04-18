import json

import pytest

from gateway.config import Config
from gateway.gemini.session import SessionManager


class _FakeStdout:
    def __init__(self, lines: list[str]):
        self._lines = [f"{line}\n".encode("utf-8") for line in lines]

    async def readline(self) -> bytes:
        if self._lines:
            return self._lines.pop(0)
        return b""


class _FakeProcess:
    def __init__(self, lines: list[str]):
        self.stdout = _FakeStdout(lines)
        self.returncode = 0

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int:
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
