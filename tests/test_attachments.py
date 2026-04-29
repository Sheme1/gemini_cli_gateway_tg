import asyncio
import os
import shutil
from pathlib import Path
from types import SimpleNamespace
import uuid
import zipfile

import pytest

from gateway.attachments import (
    AttachmentError,
    AttachmentService,
    IncomingAttachment,
    collect_incoming_attachments,
    create_sidecar,
    extract_docx_text,
    sanitize_filename,
)
from gateway.bot.handlers.attachments import (
    _AlbumDependencies,
    AttachmentAlbumCoordinator,
    process_attachment_messages,
)
from gateway.config import Config
from gateway.gemini.parser import StreamEvent
from gateway.prompt_guard import PendingPromptStore


def make_test_dir() -> Path:
    root = Path.cwd() / ".test_runtime"
    root.mkdir(exist_ok=True)
    path = root / f"attachments-{uuid.uuid4().hex}"
    path.mkdir()
    return path


class _AttachmentBot:
    def __init__(self, payloads: dict[str, bytes], file_sizes: dict[str, int] | None = None):
        self.payloads = payloads
        self.file_sizes = file_sizes or {}
        self.messages: list[dict] = []
        self.edits: list[dict] = []
        self._next_message_id = 1

    async def get_file(self, file_id: str):
        return SimpleNamespace(
            file_path=file_id,
            file_size=self.file_sizes.get(file_id, len(self.payloads[file_id])),
        )

    async def download_file(self, file_path, destination, timeout=30, **_kwargs):
        del timeout
        Path(destination).write_bytes(self.payloads[file_path])

    async def send_message(self, chat_id: int, text: str, parse_mode=None, reply_markup=None):
        message = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "reply_markup": reply_markup,
            "message_id": self._next_message_id,
        }
        self._next_message_id += 1
        self.messages.append(message)
        return SimpleNamespace(message_id=message["message_id"])

    async def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        parse_mode=None,
        reply_markup=None,
    ):
        self.edits.append(
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
                "parse_mode": parse_mode,
                "reply_markup": reply_markup,
            }
        )


class _UserSettings:
    def get_render_mode(self, _user_id: int) -> str:
        return "compact"

    def get_effective_model(self, _user_id: int, fallback_model: str) -> str:
        return fallback_model


class _SessionManager:
    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.include_directories: list[tuple[str, ...]] = []
        self.models: list[str | None] = []

    def has_active_prompt(self, _user_id: int) -> bool:
        return False

    async def send_prompt(
        self,
        prompt,
        user_id,
        on_event,
        on_approval,
        model=None,
        include_directories=(),
    ) -> None:
        del user_id, on_approval
        self.prompts.append(prompt)
        self.include_directories.append(tuple(include_directories))
        self.models.append(model)
        await on_event(
            StreamEvent(
                event_type="assistant_text",
                assistant_text="Готово.",
            )
        )
        await on_event(
            StreamEvent(
                event_type="result_stats",
                total_tokens=1,
                duration_ms=1,
                is_done=True,
            )
        )


def _config(tmp_path: Path, **kwargs) -> Config:
    return Config(
        telegram_bot_token="token",
        gemini_working_dir=str(tmp_path / "workspace"),
        gemini_artifact_roots=(str(tmp_path / "artifacts"),),
        gateway_state_dir=str(tmp_path / "state"),
        stream_update_interval=0.01,
        artifact_watch_interval=0.01,
        artifact_stable_seconds=0.01,
        **kwargs,
    )


def _message(**kwargs):
    base = {
        "chat": SimpleNamespace(id=1),
        "from_user": SimpleNamespace(id=42),
        "document": None,
        "photo": None,
        "video": None,
        "audio": None,
        "animation": None,
        "caption": None,
        "media_group_id": None,
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


def _docx_payload(text: str) -> bytes:
    tmp_path = make_test_dir()
    try:
        path = tmp_path / "sample.docx"
        document_xml = (
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f"<w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>"
        )
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("word/document.xml", document_xml)
        return path.read_bytes()
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_sanitize_filename_blocks_path_traversal_and_reserved_names() -> None:
    assert sanitize_filename("..\\../bad:name?.pdf", fallback="file") == "bad_name_.pdf"
    assert sanitize_filename("CON", fallback="file", default_extension=".txt") == "_CON.txt"
    assert sanitize_filename("", fallback="photo", default_extension=".jpg") == "photo.jpg"


def test_docx_text_sidecar_extraction() -> None:
    tmp_path = make_test_dir()
    try:
        path = tmp_path / "sample.docx"
        path.write_bytes(_docx_payload("Hello DOCX"))

        assert extract_docx_text(path) == "Hello DOCX"
        sidecar = create_sidecar(path, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")

        assert sidecar is not None
        assert "Hello DOCX" in sidecar.read_text(encoding="utf-8")
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_text_like_sidecar_extraction() -> None:
    tmp_path = make_test_dir()
    try:
        path = tmp_path / "notes.md"
        path.write_text("# Notes", encoding="utf-8")

        sidecar = create_sidecar(path, "text/markdown")

        assert sidecar is not None
        assert sidecar.read_text(encoding="utf-8").strip() == "# Notes"
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


@pytest.mark.asyncio
async def test_attachment_service_rejects_file_over_limit() -> None:
    tmp_path = make_test_dir()
    try:
        config = _config(tmp_path, attachment_max_bytes=5)
        service = AttachmentService(config)
        bot = _AttachmentBot({"doc": b"123456"}, file_sizes={"doc": 6})
        message = _message(
            document=SimpleNamespace(
                file_id="doc",
                file_name="too-large.pdf",
                mime_type="application/pdf",
                file_size=6,
            )
        )

        with pytest.raises(AttachmentError, match="слишком большой"):
            await service.prepare_bundle(
                bot=bot,
                user_id=42,
                messages=[message],
                user_prompt="read it",
            )
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_attachment_cleanup_removes_old_upload_dirs() -> None:
    tmp_path = make_test_dir()
    try:
        service = AttachmentService(_config(tmp_path, attachment_retention_days=7))
        old_dir = service.uploads_root / "tg-user-42" / "old"
        fresh_dir = service.uploads_root / "tg-user-42" / "fresh"
        old_dir.mkdir(parents=True)
        fresh_dir.mkdir(parents=True)
        old_time = 1
        os.utime(old_dir, (old_time, old_time))

        service.cleanup_old_uploads()

        assert not old_dir.exists()
        assert fresh_dir.exists()
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_collect_incoming_attachments_chooses_largest_photo() -> None:
    small = SimpleNamespace(file_id="small", file_unique_id="s", file_size=10, width=10, height=10)
    large = SimpleNamespace(file_id="large", file_unique_id="l", file_size=20, width=20, height=20)

    attachments = collect_incoming_attachments([_message(photo=[small, large])])

    assert attachments == [
        IncomingAttachment(
            kind="photo",
            file_id="large",
            original_name="photo-l.jpg",
            file_size=20,
        )
    ]


@pytest.mark.asyncio
async def test_process_attachment_document_downloads_and_passes_include_dir() -> None:
    tmp_path = make_test_dir()
    try:
        config = _config(tmp_path)
        bot = _AttachmentBot({"doc": _docx_payload("Quarterly report")})
        session_manager = _SessionManager()
        dependencies = _AlbumDependencies(
            bot=bot,
            session_manager=session_manager,  # type: ignore[arg-type]
            config=config,
            user_settings=_UserSettings(),  # type: ignore[arg-type]
            usage_ledger=None,  # type: ignore[arg-type]
            prompt_guard=PendingPromptStore(),
        )
        message = _message(
            caption="Summarize this file",
            document=SimpleNamespace(
                file_id="doc",
                file_name="report.docx",
                mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                file_size=100,
            ),
        )

        await process_attachment_messages([message], dependencies)

        assert session_manager.prompts
        lines = session_manager.prompts[0].splitlines()
        assert lines[0] == "Summarize this file"
        assert lines[1] == ""
        assert len(lines) == 4
        assert lines[2].startswith("@{")
        assert lines[2].endswith("/report.docx}")
        assert lines[3].startswith("@{")
        assert lines[3].endswith("/report.docx.txt}")
        assert "saved_path=" not in session_manager.prompts[0]
        assert "extracted_text_path=" not in session_manager.prompts[0]
        assert "Quarterly report" not in session_manager.prompts[0]
        assert session_manager.include_directories[0]
        assert Path(session_manager.include_directories[0][0]).is_dir()
        assert session_manager.models == ["auto"]
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


@pytest.mark.asyncio
async def test_process_attachment_photo_with_caption_uses_only_caption_and_native_path() -> None:
    tmp_path = make_test_dir()
    try:
        config = _config(tmp_path)
        bot = _AttachmentBot({"photo": b"jpeg"})
        session_manager = _SessionManager()
        dependencies = _AlbumDependencies(
            bot=bot,
            session_manager=session_manager,  # type: ignore[arg-type]
            config=config,
            user_settings=_UserSettings(),  # type: ignore[arg-type]
            usage_ledger=None,  # type: ignore[arg-type]
            prompt_guard=PendingPromptStore(),
        )
        message = _message(
            caption="Что изображено?",
            photo=[
                SimpleNamespace(
                    file_id="photo",
                    file_unique_id="unique",
                    file_size=4,
                    width=1000,
                    height=667,
                )
            ],
        )

        await process_attachment_messages([message], dependencies)

        prompt = session_manager.prompts[0]
        lines = prompt.splitlines()
        assert lines[0] == "Что изображено?"
        assert lines[1] == ""
        assert len(lines) == 3
        assert lines[2].startswith("@{")
        assert lines[2].endswith("/photo-unique.jpg}")
        assert "Используй только прикрепленные ниже изображения" not in prompt
        assert "без шагов анализа" not in prompt
        assert "read_file по" not in prompt
        assert "sha256=" not in prompt
        assert "mime_type=" not in prompt
        assert session_manager.models == ["auto"]
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


@pytest.mark.asyncio
async def test_process_attachment_photo_without_caption_uses_only_native_path() -> None:
    tmp_path = make_test_dir()
    try:
        config = _config(tmp_path)
        bot = _AttachmentBot({"photo": b"jpeg"})
        session_manager = _SessionManager()
        dependencies = _AlbumDependencies(
            bot=bot,
            session_manager=session_manager,  # type: ignore[arg-type]
            config=config,
            user_settings=_UserSettings(),  # type: ignore[arg-type]
            usage_ledger=None,  # type: ignore[arg-type]
            prompt_guard=PendingPromptStore(),
        )
        message = _message(
            photo=[
                SimpleNamespace(
                    file_id="photo",
                    file_unique_id="unique",
                    file_size=4,
                    width=1000,
                    height=667,
                )
            ],
        )

        await process_attachment_messages([message], dependencies)

        prompt = session_manager.prompts[0]
        assert prompt.startswith("@{")
        assert prompt.endswith("/photo-unique.jpg}")
        assert "\n" not in prompt
        assert "Проанализируй" not in prompt
        assert session_manager.models == ["auto"]
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


@pytest.mark.asyncio
async def test_process_attachment_unknown_binary_still_reaches_gemini() -> None:
    tmp_path = make_test_dir()
    try:
        config = _config(tmp_path)
        bot = _AttachmentBot({"bin": b"\x00\x01\x02"})
        session_manager = _SessionManager()
        dependencies = _AlbumDependencies(
            bot=bot,
            session_manager=session_manager,  # type: ignore[arg-type]
            config=config,
            user_settings=_UserSettings(),  # type: ignore[arg-type]
            usage_ledger=None,  # type: ignore[arg-type]
            prompt_guard=PendingPromptStore(),
        )
        message = _message(
            document=SimpleNamespace(
                file_id="bin",
                file_name="archive.bin",
                mime_type="application/octet-stream",
                file_size=3,
            ),
        )

        await process_attachment_messages([message], dependencies)

        assert session_manager.prompts[0].startswith("@{")
        assert session_manager.prompts[0].endswith("/archive.bin}")
        assert "Если бинарный формат не поддерживается" not in session_manager.prompts[0]
        assert "extracted_text_path=" not in session_manager.prompts[0]
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


@pytest.mark.asyncio
async def test_album_coordinator_combines_media_group_into_one_prompt() -> None:
    tmp_path = make_test_dir()
    try:
        config = _config(tmp_path, attachment_album_debounce_seconds=0.01)
        bot = _AttachmentBot({"a": b"a", "b": b"b"})
        session_manager = _SessionManager()
        dependencies = _AlbumDependencies(
            bot=bot,
            session_manager=session_manager,  # type: ignore[arg-type]
            config=config,
            user_settings=_UserSettings(),  # type: ignore[arg-type]
            usage_ledger=None,  # type: ignore[arg-type]
            prompt_guard=PendingPromptStore(),
        )
        coordinator = AttachmentAlbumCoordinator()

        await coordinator.add(
            _message(
                media_group_id="album-1",
                caption="Compare files",
                document=SimpleNamespace(
                    file_id="a",
                    file_name="a.txt",
                    mime_type="text/plain",
                    file_size=1,
                ),
            ),
            dependencies,
        )
        await coordinator.add(
            _message(
                media_group_id="album-1",
                document=SimpleNamespace(
                    file_id="b",
                    file_name="b.txt",
                    mime_type="text/plain",
                    file_size=1,
                ),
            ),
            dependencies,
        )
        await asyncio.sleep(0.08)

        assert len(session_manager.prompts) == 1
        lines = session_manager.prompts[0].splitlines()
        assert lines[0] == "Compare files"
        assert lines[1] == ""
        assert len(lines) == 6
        assert all(line.startswith("@{") for line in lines[2:])
        assert any(line.endswith("/a.txt}") for line in lines[2:])
        assert any(line.endswith("/a.txt.txt}") for line in lines[2:])
        assert any(line.endswith("/b.txt}") for line in lines[2:])
        assert any(line.endswith("/b.txt.txt}") for line in lines[2:])
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)
