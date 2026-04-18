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


def test_artifact_manager_fallback_scan_ignores_hidden_dirs_and_code_files() -> None:
    tmp_path = make_test_dir()
    try:
        config = build_config(tmp_path)
        manager = ArtifactManager(config)
        started_at = time.time()

        hidden_dir = tmp_path / ".gemini" / "tmp"
        hidden_dir.mkdir(parents=True)
        hidden_json = hidden_dir / "session.json"
        hidden_json.write_text("{}", encoding="utf-8")

        code_file = tmp_path / "create_docx.js"
        code_file.write_text("console.log('hi')", encoding="utf-8")

        document = tmp_path / "report.docx"
        document.write_text("ok", encoding="utf-8")

        found = manager._scan_recent_files(started_at=started_at - 1)

        assert document.resolve() in found
        assert hidden_json.resolve() not in found
        assert code_file.resolve() not in found
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_artifact_manager_ignores_sidecar_files_for_auto_send() -> None:
    tmp_path = make_test_dir()
    try:
        config = build_config(tmp_path)
        manager = ArtifactManager(config)
        started_at = time.time()

        docx = tmp_path / "referat.docx"
        markdown = tmp_path / "referat.md"
        lockfile = tmp_path / "package-lock.json"
        docx.write_text("docx", encoding="utf-8")
        markdown.write_text("markdown", encoding="utf-8")
        lockfile.write_text("{}", encoding="utf-8")

        found = manager._scan_recent_files(started_at=started_at - 1)

        assert docx.resolve() in found
        assert markdown.resolve() not in found
        assert lockfile.resolve() not in found
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_artifact_manager_explicit_send_file_can_send_markdown() -> None:
    tmp_path = make_test_dir()
    try:
        config = build_config(tmp_path)
        manager = ArtifactManager(config)
        markdown = tmp_path / "referat.md"
        markdown.write_text("markdown", encoding="utf-8")

        manager.register_event(
            StreamEvent(
                event_type="assistant_text",
                file_candidates=["referat.md"],
                direct_file_candidates=["referat.md"],
            )
        )

        resolved = manager._resolve_candidates(
            ["referat.md"],
            allow_non_deliverable=True,
        )
        auto_resolved = manager._resolve_candidates(["referat.md"])

        assert resolved == [markdown.resolve()]
        assert auto_resolved == []
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_artifact_manager_waits_for_stable_file_before_sending() -> None:
    tmp_path = make_test_dir()
    try:
        config = Config(
            telegram_bot_token="token",
            gemini_working_dir=str(tmp_path),
            gemini_artifact_roots=(str(tmp_path),),
            artifact_stable_seconds=5.0,
        )
        manager = ArtifactManager(config)
        document = tmp_path / "report.docx"
        document.write_text("ok", encoding="utf-8")
        manager.register_event(
            StreamEvent(
                event_type="assistant_text",
                file_candidates=["report.docx"],
                direct_file_candidates=["report.docx"],
            )
        )

        first = manager._ready_artifacts(started_at=time.time(), now=100.0)
        second = manager._ready_artifacts(started_at=time.time(), now=103.0)
        third = manager._ready_artifacts(started_at=time.time(), now=105.1)

        assert first == []
        assert second == []
        assert third == [document.resolve()]
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)
