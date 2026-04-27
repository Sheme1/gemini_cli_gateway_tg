from pathlib import Path
import shutil
import uuid

from gateway.config import Config
from gateway.user_environment import UserEnvironmentResolver


def make_test_dir() -> Path:
    root = Path.cwd() / ".test_runtime"
    root.mkdir(exist_ok=True)
    path = root / f"user-env-{uuid.uuid4().hex}"
    path.mkdir()
    return path


def test_user_environment_resolver_defaults_to_legacy_paths() -> None:
    tmp_path = make_test_dir()
    try:
        config = Config(
            telegram_bot_token="token",
            gemini_working_dir=str(tmp_path),
            gemini_artifact_roots=(str(tmp_path),),
            gateway_state_dir=str(tmp_path / "state"),
        )
        resolver = UserEnvironmentResolver(config)

        assert resolver.enabled is False
        assert resolver.working_dir_for(123) == str(tmp_path)
        assert resolver.artifact_roots_for(123) == (str(tmp_path),)
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_user_environment_resolver_creates_stable_per_user_paths() -> None:
    tmp_path = make_test_dir()
    try:
        config = Config(
            telegram_bot_token="token",
            gemini_working_dir=str(tmp_path / "legacy"),
            gemini_artifact_roots=(str(tmp_path / "legacy"),),
            gateway_state_dir=str(tmp_path / "state"),
            gateway_experimental_multi_user_workspaces=True,
            gateway_user_workspaces_dir=str(tmp_path / "users"),
        )
        resolver = UserEnvironmentResolver(config)

        environment = resolver.for_user(123456789)

        assert environment.root_dir == tmp_path / "users" / "tg-user-123456789"
        assert environment.workspace_dir.is_dir()
        assert environment.artifacts_dir.is_dir()
        assert environment.gemini_md_path == environment.workspace_dir / "GEMINI.md"
        assert "tg-user-123456789" in resolver.working_dir_for(123456789)
        assert resolver.artifact_roots_for(123456789) == (
            str(environment.workspace_dir),
            str(environment.artifacts_dir),
        )
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)
