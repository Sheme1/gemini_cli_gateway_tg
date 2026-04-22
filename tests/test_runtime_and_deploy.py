from pathlib import Path
import shutil
from types import SimpleNamespace
import uuid

import pytest

from gateway.config import Config
from gateway.runtime import (
    CommandProbe,
    GatewayRuntimeState,
    PromptLatencySnapshot,
    build_status_text,
    startup_preflight,
)


def make_test_dir() -> Path:
    root = Path.cwd() / ".test_runtime"
    root.mkdir(exist_ok=True)
    path = root / f"runtime-{uuid.uuid4().hex}"
    path.mkdir()
    return path


def test_config_parses_new_runtime_env(monkeypatch) -> None:
    tmp_path = make_test_dir()
    try:
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456789:secret-token")
        monkeypatch.setenv("GEMINI_WORKING_DIR", str(tmp_path))
        include_dir = tmp_path / "include"
        include_dir.mkdir()
        monkeypatch.setenv("GEMINI_INCLUDE_DIRECTORIES", str(include_dir))
        monkeypatch.setenv("GEMINI_BIN", "gemini-custom")
        monkeypatch.setenv("POLLING_TIMEOUT", "11")
        monkeypatch.setenv("POLLING_CONCURRENCY_LIMIT", "7")
        monkeypatch.setenv("STREAM_MIN_UPDATE_CHARS", "88")
        monkeypatch.setenv("STREAM_RETRY_MAX_DELAY", "12")
        monkeypatch.setenv("GATEWAY_STATE_DIR", str(tmp_path / "state"))

        config = Config.from_env()

        assert config.gemini_bin == "gemini-custom"
        assert config.gemini_include_directories == (str(include_dir.resolve()),)
        assert config.include_directories_flag == [
            "--include-directories",
            str(include_dir.resolve()),
        ]
        assert config.polling_timeout == 11
        assert config.polling_concurrency_limit == 7
        assert config.stream_min_update_chars == 88
        assert config.stream_retry_max_delay == 12
        assert config.gateway_state_dir == str((tmp_path / "state").resolve())
        assert config.redacted_dict()["telegram_bot_token"] == "1234...oken"
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_systemd_unit_contains_expected_restart_directives() -> None:
    unit = Path("telegram-gateway.service").read_text(encoding="utf-8")

    assert "StartLimitIntervalSec=300" in unit
    assert "StartLimitBurst=5" in unit
    assert "Restart=on-failure" in unit
    assert "RestartSec=10s" in unit
    assert "TimeoutStopSec=30s" in unit
    assert "Environment=HOME=__HOME_DIR__" in unit
    assert "Environment=PATH=__SERVICE_PATH__" in unit


@pytest.mark.asyncio
async def test_status_text_includes_last_prompt_latency() -> None:
    class _SessionManager:
        def active_prompt_count(self) -> int:
            return 0

        def active_prompt_users(self) -> list[int]:
            return []

    config = Config(
        telegram_bot_token="token",
        gemini_working_dir=".",
        gemini_artifact_roots=(".",),
    )
    state = GatewayRuntimeState()
    state.record_prompt_latency(
        PromptLatencySnapshot(
            user_id=42,
            started_at=state.started_at,
            process_spawn_ms=12,
            init_ms=300,
            first_text_ms=900,
            total_ms=1500,
            returncode=0,
        )
    )

    text = await build_status_text(config, state, _SessionManager())  # type: ignore[arg-type]

    assert "Последний запрос" in text
    assert "first_text=900ms" in text
    assert "total=1.5s" in text


class _FakeBot:
    def __init__(self) -> None:
        self.webhook_url = "https://example.com/hook"
        self.delete_calls: list[bool | None] = []

    async def get_me(self, request_timeout=None):
        return SimpleNamespace(id=100, username="gateway_bot", full_name="Gateway")

    async def get_webhook_info(self, request_timeout=None):
        return SimpleNamespace(url=self.webhook_url, pending_update_count=3)

    async def delete_webhook(self, drop_pending_updates=None, request_timeout=None):
        self.delete_calls.append(drop_pending_updates)
        self.webhook_url = ""
        return True


@pytest.mark.asyncio
async def test_startup_preflight_deletes_existing_webhook(monkeypatch) -> None:
    tmp_path = make_test_dir()
    try:

        async def fake_probe(command: str, *_args, cwd=None):
            return CommandProbe(command=command, path=f"/bin/{command}", version="1.0")

        monkeypatch.setattr("gateway.runtime.probe_command", fake_probe)

        config = Config(
            telegram_bot_token="token",
            gemini_working_dir=str(tmp_path),
            gemini_artifact_roots=(str(tmp_path),),
            gateway_state_dir=str(tmp_path / "state"),
        )
        bot = _FakeBot()
        state = GatewayRuntimeState()

        await startup_preflight(config, bot, state)

        assert bot.delete_calls == [False]
        assert state.webhook_url == ""
        assert state.bot_username == "gateway_bot"
        assert state.gemini_probe.version == "1.0"
        assert (tmp_path / "state").is_dir()
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)
