import asyncio
import json

import pytest

from gateway.config import Config
from gateway.gemini.session import (
    SessionManager,
    parse_gemini_sessions_output,
)


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


def test_parse_gemini_sessions_output_newest_first_and_current() -> None:
    output = """
Available sessions for this project (3):

  1. Old auth fix (2 days ago) [11111111-1111-4111-8111-111111111111]
  2. Middle topic with (notes) (5 hours ago) [22222222]
  3. Latest deploy check (Just now, current) [33333333-3333-4333-8333-333333333333]
[WARN] Skipping unreadable directory: tmp
"""

    sessions = parse_gemini_sessions_output(output)

    assert [session.title for session in sessions] == [
        "Latest deploy check",
        "Middle topic with (notes)",
        "Old auth fix",
    ]
    assert sessions[0].is_current is True
    assert sessions[0].relative_time == "Just now"
    assert sessions[0].short_id == "33333333..."
    assert sessions[1].session_id == "22222222"


def test_parse_gemini_sessions_output_ignores_empty_and_warning_output() -> None:
    output = "No previous sessions found for this project.\n[WARN] skipped"

    assert parse_gemini_sessions_output(output) == []


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


@pytest.mark.asyncio
async def test_session_manager_passes_include_directories(monkeypatch) -> None:
    captured_args = []

    lines = [
        json.dumps(
            {
                "type": "result",
                "status": "success",
                "stats": {"total_tokens": 1, "duration_ms": 10},
            }
        ),
    ]

    async def fake_create_subprocess_exec(*args, **_kwargs):
        captured_args.extend(args)
        return _FakeProcess(lines)

    monkeypatch.setattr(
        "gateway.gemini.session.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    config = Config(
        telegram_bot_token="token",
        gemini_working_dir=".",
        gemini_artifact_roots=(".",),
        gemini_include_directories=("/repo/shared", "/repo/docs"),
    )
    manager = SessionManager(config)

    async def on_event(_event):
        return None

    async def on_approval(_req):
        raise AssertionError("approval request was not expected")

    await manager.send_prompt(
        prompt="test",
        user_id=123,
        on_event=on_event,
        on_approval=on_approval,
        model="gemini-2.5-flash",
    )

    assert captured_args[captured_args.index("-m") + 1] == "gemini-2.5-flash"
    assert "--include-directories" in captured_args
    assert captured_args[captured_args.index("--include-directories") + 1] == (
        "/repo/shared,/repo/docs"
    )


@pytest.mark.asyncio
async def test_session_manager_deletes_session_by_source_index(monkeypatch) -> None:
    captured_args = []

    async def fake_get_sessions_list():
        return [
            parse_gemini_sessions_output(
                "1. Old (2 days ago) [old-session]\n2. New (Just now) [new-session]\n"
            )[0]
        ]

    class _DeleteProcess:
        returncode = 0

        async def communicate(self):
            return b"deleted", b""

    async def fake_create_subprocess_exec(*args, **_kwargs):
        captured_args.extend(args)
        return _DeleteProcess()

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
    manager.active_sessions[42] = "new-session"
    monkeypatch.setattr(manager, "get_sessions_list", fake_get_sessions_list)

    deleted = await manager.delete_session_by_id("new-session")

    assert deleted is True
    assert captured_args[0].lower().endswith(("gemini", "gemini.cmd"))
    assert captured_args[1:] == ["--delete-session", "2"]
    assert manager.get_active_session(42) is None
