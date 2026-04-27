from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from gateway.config import Config


@dataclass(frozen=True)
class UserEnvironment:
    """Per-user filesystem scope for experimental multi-user mode."""

    user_id: int
    root_dir: Path
    workspace_dir: Path
    artifacts_dir: Path
    profile_path: Path
    gemini_md_path: Path

    @property
    def artifact_roots(self) -> tuple[str, ...]:
        return (str(self.workspace_dir), str(self.artifacts_dir))

    def ensure_directories(self) -> None:
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)


class UserEnvironmentResolver:
    """Resolves legacy or per-user Gemini CLI working directories."""

    def __init__(self, config: Config):
        self.config = config
        self.base_dir = Path(config.gateway_user_workspaces_dir).expanduser().resolve()

    @property
    def enabled(self) -> bool:
        return self.config.gateway_experimental_multi_user_workspaces

    def for_user(self, user_id: int) -> UserEnvironment:
        normalized_user_id = int(user_id)
        root_dir = self.base_dir / f"tg-user-{normalized_user_id}"
        environment = UserEnvironment(
            user_id=normalized_user_id,
            root_dir=root_dir,
            workspace_dir=root_dir / "workspace",
            artifacts_dir=root_dir / "artifacts",
            profile_path=root_dir / "profile.json",
            gemini_md_path=root_dir / "workspace" / "GEMINI.md",
        )
        environment.ensure_directories()
        return environment

    def working_dir_for(self, user_id: int | None = None) -> str:
        if self.enabled and user_id is not None:
            return str(self.for_user(user_id).workspace_dir)
        return self.config.gemini_working_dir

    def artifact_roots_for(self, user_id: int | None = None) -> tuple[str, ...]:
        if self.enabled and user_id is not None:
            return self.for_user(user_id).artifact_roots
        return self.config.gemini_artifact_roots

    def describe_for(self, user_id: int | None = None) -> dict[str, str]:
        if not self.enabled:
            return {
                "mode": "legacy",
                "working_dir": self.config.gemini_working_dir,
                "artifact_roots": ", ".join(self.config.gemini_artifact_roots),
                "shared_auth": "yes",
            }

        if user_id is None:
            return {
                "mode": "multi-user",
                "working_dir": str(self.base_dir),
                "artifact_roots": "per-user",
                "shared_auth": "yes",
            }

        environment = self.for_user(user_id)
        return {
            "mode": "multi-user",
            "working_dir": str(environment.workspace_dir),
            "artifact_roots": ", ".join(environment.artifact_roots),
            "profile": str(environment.profile_path),
            "gemini_md": str(environment.gemini_md_path),
            "shared_auth": "yes",
        }
