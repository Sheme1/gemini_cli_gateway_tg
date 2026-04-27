from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace
import uuid

import pytest

from gateway.config import Config
from gateway.doctor import run_doctor
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
        policy_file = tmp_path / "policy.toml"
        admin_policy_file = tmp_path / "admin-policy.toml"
        policy_file.write_text("[[rule]]\n", encoding="utf-8")
        admin_policy_file.write_text("[[rule]]\n", encoding="utf-8")
        monkeypatch.setenv("GEMINI_POLICY_PATHS", str(policy_file))
        monkeypatch.setenv("GEMINI_ADMIN_POLICY_PATHS", str(admin_policy_file))
        monkeypatch.setenv("GEMINI_ALLOWED_MCP_SERVER_NAMES", "github,context7")
        monkeypatch.setenv("GEMINI_EXTENSIONS", "none")
        monkeypatch.setenv("GEMINI_SCREEN_READER", "true")
        monkeypatch.setenv("GEMINI_BIN", "gemini-custom")
        monkeypatch.setenv("POLLING_TIMEOUT", "11")
        monkeypatch.setenv("POLLING_CONCURRENCY_LIMIT", "7")
        monkeypatch.setenv("STREAM_MIN_UPDATE_CHARS", "88")
        monkeypatch.setenv("STREAM_RETRY_MAX_DELAY", "12")
        monkeypatch.setenv("PROMPT_WARN_CHARS", "99")
        monkeypatch.setenv("PROMPT_MAX_CHARS", "999")
        monkeypatch.setenv("PROMPT_CONFIRM_TIMEOUT", "33")
        monkeypatch.setenv("USER_DAILY_TOKEN_LIMIT", "1000")
        monkeypatch.setenv("GLOBAL_DAILY_TOKEN_LIMIT", "2000")
        monkeypatch.delenv("GEMINI_TARGET_VERSION", raising=False)
        monkeypatch.setenv("GEMINI_SKIP_TRUST", "false")
        monkeypatch.setenv("LOG_MODE", "debug")
        monkeypatch.delenv("LOG_LEVEL", raising=False)
        monkeypatch.setenv("GATEWAY_STATE_DIR", str(tmp_path / "state"))
        monkeypatch.setenv("GATEWAY_EXPERIMENTAL_MULTI_USER_WORKSPACES", "true")
        monkeypatch.setenv("GATEWAY_USER_WORKSPACES_DIR", str(tmp_path / "users"))

        config = Config.from_env()

        assert config.gemini_bin == "gemini-custom"
        assert config.gemini_target_version == "0.39.1"
        assert config.gemini_skip_trust is False
        assert config.gemini_include_directories == (str(include_dir.resolve()),)
        assert config.gemini_policy_paths == (str(policy_file),)
        assert config.gemini_admin_policy_paths == (str(admin_policy_file),)
        assert config.gemini_allowed_mcp_server_names == ("github", "context7")
        assert config.gemini_extensions == ("none",)
        assert config.gemini_screen_reader is True
        assert config.include_directories_flag == [
            "--include-directories",
            str(include_dir.resolve()),
        ]
        assert config.policy_flags == ["--policy", str(policy_file)]
        assert config.admin_policy_flags == ["--admin-policy", str(admin_policy_file)]
        assert config.allowed_mcp_server_names_flag == [
            "--allowed-mcp-server-names",
            "github",
            "--allowed-mcp-server-names",
            "context7",
        ]
        assert config.extensions_flag == ["--extensions", "none"]
        assert config.screen_reader_flag == ["--screen-reader"]
        assert config.polling_timeout == 11
        assert config.polling_concurrency_limit == 7
        assert config.stream_min_update_chars == 88
        assert config.stream_retry_max_delay == 12
        assert config.prompt_warn_chars == 99
        assert config.prompt_max_chars == 999
        assert config.prompt_confirm_timeout == 33
        assert config.user_daily_token_limit == 1000
        assert config.global_daily_token_limit == 2000
        assert config.log_mode == "debug"
        assert config.log_level == "DEBUG"
        assert config.gateway_state_dir == str((tmp_path / "state").resolve())
        assert config.gateway_experimental_multi_user_workspaces is True
        assert config.gateway_user_workspaces_dir == str((tmp_path / "users").resolve())
        assert config.redacted_dict()["gemini_skip_trust"] is False
        assert (
            config.redacted_dict()["gateway_experimental_multi_user_workspaces"] is True
        )
        assert config.redacted_dict()["telegram_bot_token"] == "1234...oken"
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


@pytest.mark.parametrize(
    ("raw_chat_id", "expected_first", "expected_ids"),
    [
        ("", None, ()),
        ("111111111", 111111111, (111111111,)),
        ("111111111,222222222", 111111111, (111111111, 222222222)),
        ("111111111,-1002222222222", 111111111, (111111111, -1002222222222)),
    ],
)
def test_config_parses_target_chat_id_allowlist(
    monkeypatch,
    raw_chat_id: str,
    expected_first: int | None,
    expected_ids: tuple[int, ...],
) -> None:
    tmp_path = make_test_dir()
    try:
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456789:secret-token")
        monkeypatch.setenv("GEMINI_WORKING_DIR", str(tmp_path))
        monkeypatch.setenv("TARGET_CHAT_ID", raw_chat_id)

        config = Config.from_env()

        assert config.target_chat_id == expected_first
        assert config.target_chat_ids == expected_ids
        assert config.allowed_target_chat_ids == expected_ids
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_config_rejects_invalid_target_chat_id(monkeypatch) -> None:
    tmp_path = make_test_dir()
    try:
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456789:secret-token")
        monkeypatch.setenv("GEMINI_WORKING_DIR", str(tmp_path))
        monkeypatch.setenv("TARGET_CHAT_ID", "111111111,not-a-number")

        with pytest.raises(ValueError, match="TARGET_CHAT_ID.*not-a-number"):
            Config.from_env()
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


def test_update_script_contains_safe_update_flow() -> None:
    script = Path("update.sh").read_text(encoding="utf-8")

    assert 'git -C "${PROJECT_DIR}" pull --ff-only' in script
    assert "-m pip install -r" in script
    assert "-m gateway.main --doctor" in script
    assert "systemctl restart" in script
    assert "daemon-reload" in script
    assert "cmp -s" in script


def test_update_script_has_valid_bash_syntax() -> None:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is not available on this host")

    result = subprocess.run(
        [bash, "-n", "update.sh"],
        check=False,
        capture_output=True,
        text=True,
    )

    bash_output = (result.stdout + result.stderr).replace("\x00", "")
    if "HCS_E_HYPERV_NOT_INSTALLED" in bash_output or "WSL" in bash_output:
        pytest.skip("bash resolves to WSL launcher, but WSL is unavailable")

    assert result.returncode == 0, result.stderr


def test_readmes_document_multi_chat_id_and_update_flow() -> None:
    readme_en = Path("README.md").read_text(encoding="utf-8")
    readme_ru = Path("README.ru.md").read_text(encoding="utf-8")
    env_example = Path(".env.example").read_text(encoding="utf-8")

    for text in (readme_en, readme_ru, env_example):
        assert "TARGET_CHAT_ID=111111111,222222222" in text
        assert "GATEWAY_EXPERIMENTAL_MULTI_USER_WORKSPACES" in text
    for text in (readme_en, readme_ru):
        assert "update.sh" in text
        assert "git pull --ff-only" in text
        assert "daemon-reload" in text
        assert "/init" in text


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


@pytest.mark.asyncio
async def test_doctor_warns_on_gemini_version_mismatch(monkeypatch) -> None:
    tmp_path = make_test_dir()
    try:
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456789:secret-token")
        monkeypatch.setenv("GEMINI_WORKING_DIR", str(tmp_path))
        monkeypatch.setenv("GATEWAY_STATE_DIR", str(tmp_path / "state"))
        monkeypatch.setenv("GEMINI_TARGET_VERSION", "0.39.1")

        async def fake_probe(command: str, *_args, cwd=None):
            del cwd
            version = "0.40.0" if "gemini" in command else "v22.0.0"
            return CommandProbe(
                command=command, path=f"/bin/{command}", version=version
            )

        monkeypatch.setattr("gateway.doctor.probe_command", fake_probe)

        report = await run_doctor()

        gemini_check = next(check for check in report.checks if check.name == "gemini")
        assert gemini_check.status == "warn"
        assert "0.39.1" in gemini_check.hint
        assert not report.has_errors
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


@pytest.mark.asyncio
async def test_doctor_warns_when_headless_trust_is_disabled(monkeypatch) -> None:
    tmp_path = make_test_dir()
    try:
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456789:secret-token")
        monkeypatch.setenv("GEMINI_WORKING_DIR", str(tmp_path))
        monkeypatch.setenv("GATEWAY_STATE_DIR", str(tmp_path / "state"))
        monkeypatch.setenv("GEMINI_SKIP_TRUST", "false")
        monkeypatch.delenv("GEMINI_CLI_TRUST_WORKSPACE", raising=False)

        async def fake_probe(command: str, *_args, cwd=None):
            del cwd
            version = "0.39.1" if "gemini" in command else "v22.0.0"
            return CommandProbe(
                command=command, path=f"/bin/{command}", version=version
            )

        monkeypatch.setattr("gateway.doctor.probe_command", fake_probe)

        report = await run_doctor()

        trust_check = next(
            check for check in report.checks if check.name == "headless trust"
        )
        assert trust_check.status == "warn"
        assert "GEMINI_SKIP_TRUST=true" in trust_check.hint
        assert not report.has_errors
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)
