import time
import uuid
from pathlib import Path
import shutil

from gateway.artifacts import ArtifactManager
from gateway.config import Config
from gateway.gemini.parser import StreamEvent


def make_test_dir() -> Path:
    root = Path.cwd() / ".test_runtime"
    root.mkdir(exist_ok=True)
    path = root / f"artifacts-{uuid.uuid4().hex}"
    path.mkdir()
    return path


def build_config(tmp_path: Path) -> Config:
    return Config(
        telegram_bot_token="token",
        gemini_working_dir=str(tmp_path),
        gemini_artifact_roots=(str(tmp_path),),
    )


def test_artifact_manager_resolves_relative_candidate() -> None:
    tmp_path = make_test_dir()
    try:
        config = build_config(tmp_path)
        target = tmp_path / "out" / "report.docx"
        target.parent.mkdir(parents=True)
        target.write_text("ok", encoding="utf-8")

        manager = ArtifactManager(config)
        manager.register_event(
            StreamEvent(
                event_type="assistant_text",
                file_candidates=["out/report.docx"],
                direct_file_candidates=["out/report.docx"],
            )
        )

        resolved = manager._resolve_candidates(["out/report.docx"])

        assert resolved == [target.resolve()]
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_artifact_manager_scans_recent_files() -> None:
    tmp_path = make_test_dir()
    try:
        config = build_config(tmp_path)
        manager = ArtifactManager(config)
        created = tmp_path / "generated.pdf"
        started_at = time.time()
        created.write_text("pdf", encoding="utf-8")

        found = manager._scan_recent_files(started_at=started_at - 1)

        assert created.resolve() in found
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)
