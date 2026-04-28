from __future__ import annotations

import logging
import mimetypes
import re
import shutil
import time
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree

from gateway.config import Config

logger = logging.getLogger(__name__)

_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
_TEXT_EXTENSIONS = {
    ".bat",
    ".c",
    ".cfg",
    ".conf",
    ".cpp",
    ".cs",
    ".css",
    ".csv",
    ".env",
    ".go",
    ".h",
    ".hpp",
    ".htm",
    ".html",
    ".ini",
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".log",
    ".md",
    ".php",
    ".ps1",
    ".py",
    ".rb",
    ".rs",
    ".sh",
    ".sql",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
_DEFAULT_EXTENSIONS = {
    "animation": ".mp4",
    "audio": ".mp3",
    "document": ".bin",
    "photo": ".jpg",
    "video": ".mp4",
}


class AttachmentError(Exception):
    """User-facing attachment preparation failure."""

    def __init__(self, user_message: str):
        super().__init__(user_message)
        self.user_message = user_message


@dataclass(frozen=True)
class IncomingAttachment:
    kind: str
    file_id: str
    original_name: str
    mime_type: str | None = None
    file_size: int | None = None


@dataclass(frozen=True)
class PreparedAttachment:
    kind: str
    original_name: str
    saved_path: Path
    mime_type: str | None
    file_size: int | None
    sidecar_path: Path | None = None


@dataclass(frozen=True)
class AttachmentBundle:
    prompt_text: str
    include_dirs: tuple[str, ...]
    saved_file_paths: tuple[Path, ...]
    sidecar_paths: tuple[Path, ...]


class AttachmentService:
    """Downloads Telegram attachments and builds Gemini-readable prompt context."""

    def __init__(self, config: Config):
        self.config = config
        self.uploads_root = (
            Path(config.gateway_state_dir).expanduser().resolve() / "uploads"
        )

    async def prepare_bundle(
        self,
        *,
        bot: Any,
        user_id: int,
        messages: Iterable[Any],
        user_prompt: str,
    ) -> AttachmentBundle:
        self.cleanup_old_uploads()
        incoming = collect_incoming_attachments(messages)
        if not incoming:
            raise AttachmentError("Не нашёл поддерживаемое вложение в сообщении.")

        request_dir = self._new_request_dir(user_id)
        prepared: list[PreparedAttachment] = []
        try:
            for index, attachment in enumerate(incoming, start=1):
                prepared.append(
                    await self._download_attachment(
                        bot=bot,
                        request_dir=request_dir,
                        attachment=attachment,
                        index=index,
                    )
                )
        except Exception:
            shutil.rmtree(request_dir, ignore_errors=True)
            raise

        return AttachmentBundle(
            prompt_text=build_attachment_prompt(user_prompt, prepared),
            include_dirs=(str(request_dir),),
            saved_file_paths=tuple(item.saved_path for item in prepared),
            sidecar_paths=tuple(
                item.sidecar_path for item in prepared if item.sidecar_path is not None
            ),
        )

    def cleanup_old_uploads(self) -> None:
        retention_days = self.config.attachment_retention_days
        if retention_days < 0 or not self.uploads_root.exists():
            return

        threshold = time.time() - retention_days * 24 * 60 * 60
        for user_dir in self.uploads_root.glob("tg-user-*"):
            if not user_dir.is_dir():
                continue
            for request_dir in user_dir.iterdir():
                if not request_dir.is_dir():
                    continue
                try:
                    if request_dir.stat().st_mtime < threshold:
                        shutil.rmtree(request_dir, ignore_errors=True)
                except OSError:
                    continue

    def _new_request_dir(self, user_id: int) -> Path:
        request_id = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
        path = self.uploads_root / f"tg-user-{int(user_id)}" / request_id
        path.mkdir(parents=True, exist_ok=False)
        return path

    async def _download_attachment(
        self,
        *,
        bot: Any,
        request_dir: Path,
        attachment: IncomingAttachment,
        index: int,
    ) -> PreparedAttachment:
        _ensure_size_allowed(attachment.file_size, self.config.attachment_max_bytes)

        telegram_file = await bot.get_file(attachment.file_id)
        file_size = getattr(telegram_file, "file_size", None) or attachment.file_size
        _ensure_size_allowed(file_size, self.config.attachment_max_bytes)

        file_path = getattr(telegram_file, "file_path", None)
        if not file_path:
            raise AttachmentError("Telegram не вернул путь для скачивания файла.")

        filename = sanitize_filename(
            attachment.original_name,
            fallback=f"{index:02d}-{attachment.kind}",
            default_extension=_extension_for(attachment, file_path),
        )
        destination = _dedupe_destination(request_dir / filename)

        await bot.download_file(
            file_path,
            destination=destination,
            timeout=self.config.attachment_download_timeout,
        )
        sidecar_path = create_sidecar(destination, attachment.mime_type)

        return PreparedAttachment(
            kind=attachment.kind,
            original_name=attachment.original_name,
            saved_path=destination.resolve(),
            mime_type=attachment.mime_type,
            file_size=file_size,
            sidecar_path=sidecar_path.resolve() if sidecar_path else None,
        )


def collect_incoming_attachments(messages: Iterable[Any]) -> list[IncomingAttachment]:
    attachments: list[IncomingAttachment] = []
    seen_file_ids: set[str] = set()
    for message in messages:
        for attachment in _attachments_from_message(message):
            if attachment.file_id in seen_file_ids:
                continue
            seen_file_ids.add(attachment.file_id)
            attachments.append(attachment)
    return attachments


def caption_prompt(messages: Iterable[Any]) -> str:
    captions = [
        str(caption).strip()
        for message in messages
        if (caption := getattr(message, "caption", None))
        and str(caption).strip()
    ]
    if captions:
        return "\n\n".join(captions)
    return "Проанализируй прикрепленные файлы и ответь по их содержимому."


def build_attachment_prompt(
    user_prompt: str,
    attachments: Iterable[PreparedAttachment],
) -> str:
    lines = [
        "Пользователь прикрепил файлы к сообщению Telegram.",
        (
            "Файлы сохранены на сервере и доступны Gemini CLI через read_file, "
            "потому что их папка передана в --include-directories."
        ),
        (
            "Перед ответом прочитай релевантные вложения через read_file по "
            "saved_path. Для DOCX и текстовых файлов сначала используй "
            "extracted_text_path, если он есть; при необходимости сверяйся с "
            "оригиналом."
        ),
        (
            "Изображения, PDF, audio и video читай из оригинального saved_path. "
            "Если бинарный формат не поддерживается Gemini CLI, честно скажи об "
            "этом и работай с доступными метаданными."
        ),
        "",
        "Вложения:",
    ]
    for index, attachment in enumerate(attachments, start=1):
        lines.extend(
            [
                f"{index}. type={attachment.kind}",
                f"   original_name={attachment.original_name}",
                f"   saved_path={_prompt_path(attachment.saved_path)}",
                f"   mime_type={attachment.mime_type or 'unknown'}",
                f"   size_bytes={attachment.file_size or 'unknown'}",
            ]
        )
        if attachment.sidecar_path is not None:
            lines.append(f"   extracted_text_path={_prompt_path(attachment.sidecar_path)}")

    prompt = user_prompt.strip() or (
        "Проанализируй прикрепленные файлы и ответь по их содержимому."
    )
    lines.extend(["", "Запрос пользователя:", prompt])
    return "\n".join(lines)


def sanitize_filename(
    name: str | None,
    *,
    fallback: str,
    default_extension: str = "",
) -> str:
    raw_name = (name or "").replace("\\", "/").split("/")[-1]
    raw_name = raw_name.strip()
    if not raw_name:
        raw_name = fallback

    sanitized = "".join(
        "_" if char in '<>:"/\\|?*' or ord(char) < 32 else char
        for char in raw_name
    )
    sanitized = re.sub(r"\s+", " ", sanitized).strip(" .")
    if not sanitized or sanitized in {".", ".."}:
        sanitized = fallback

    path = Path(sanitized)
    suffix = path.suffix
    if not suffix and default_extension:
        sanitized += default_extension
        suffix = default_extension

    stem = sanitized[: -len(suffix)] if suffix else sanitized
    if stem.upper() in _WINDOWS_RESERVED_NAMES:
        sanitized = f"_{sanitized}"

    if len(sanitized) > 120:
        suffix = Path(sanitized).suffix
        stem = sanitized[: -len(suffix)] if suffix else sanitized
        sanitized = f"{stem[: 120 - len(suffix)]}{suffix}"
    return sanitized


def create_sidecar(path: Path, mime_type: str | None) -> Path | None:
    suffix = path.suffix.lower()
    try:
        if suffix == ".docx":
            text = extract_docx_text(path)
        elif _is_text_like(path, mime_type):
            text = path.read_text(encoding="utf-8", errors="replace")
        else:
            return None
    except Exception as exc:
        logger.warning("Failed to extract attachment sidecar for %s: %s", path, exc)
        return None

    text = text.strip()
    if not text:
        return None
    sidecar_path = path.with_name(f"{path.name}.txt")
    sidecar_path.write_text(text + "\n", encoding="utf-8")
    return sidecar_path


def extract_docx_text(path: Path) -> str:
    chunks: list[str] = []
    with zipfile.ZipFile(path) as archive:
        names = [
            name
            for name in archive.namelist()
            if name == "word/document.xml"
            or re.fullmatch(r"word/(header|footer)\d+\.xml", name)
            or name in {"word/footnotes.xml", "word/endnotes.xml"}
        ]
        for name in names:
            try:
                root = ElementTree.fromstring(archive.read(name))
            except ElementTree.ParseError:
                continue
            _append_docx_node_text(root, chunks)
            chunks.append("\n")
    return re.sub(r"\n{3,}", "\n\n", "".join(chunks)).strip()


def _append_docx_node_text(node: ElementTree.Element, chunks: list[str]) -> None:
    tag = node.tag.rsplit("}", maxsplit=1)[-1]
    if tag == "t" and node.text:
        chunks.append(node.text)
    elif tag == "tab":
        chunks.append("\t")
    elif tag in {"br", "cr", "p"}:
        chunks.append("\n")
    for child in list(node):
        _append_docx_node_text(child, chunks)


def _attachments_from_message(message: Any) -> list[IncomingAttachment]:
    result: list[IncomingAttachment] = []
    if document := getattr(message, "document", None):
        result.append(_attachment_from_downloadable("document", document))
    if photos := getattr(message, "photo", None):
        photo = max(
            photos,
            key=lambda item: (
                getattr(item, "file_size", 0) or 0,
                (getattr(item, "width", 0) or 0) * (getattr(item, "height", 0) or 0),
            ),
        )
        result.append(_attachment_from_downloadable("photo", photo))
    if video := getattr(message, "video", None):
        result.append(_attachment_from_downloadable("video", video))
    if audio := getattr(message, "audio", None):
        result.append(_attachment_from_downloadable("audio", audio))
    if animation := getattr(message, "animation", None):
        result.append(_attachment_from_downloadable("animation", animation))
    return result


def _attachment_from_downloadable(kind: str, downloadable: Any) -> IncomingAttachment:
    file_id = str(getattr(downloadable, "file_id", "") or "")
    if not file_id:
        raise AttachmentError("Telegram прислал вложение без file_id.")

    original_name = (
        getattr(downloadable, "file_name", None)
        or getattr(downloadable, "filename", None)
        or _fallback_name(kind, downloadable)
    )
    return IncomingAttachment(
        kind=kind,
        file_id=file_id,
        original_name=str(original_name),
        mime_type=getattr(downloadable, "mime_type", None),
        file_size=getattr(downloadable, "file_size", None),
    )


def _fallback_name(kind: str, downloadable: Any) -> str:
    unique = (
        getattr(downloadable, "file_unique_id", None)
        or getattr(downloadable, "file_id", None)
        or uuid.uuid4().hex[:8]
    )
    return f"{kind}-{unique}{_DEFAULT_EXTENSIONS.get(kind, '.bin')}"


def _extension_for(attachment: IncomingAttachment, telegram_file_path: str) -> str:
    suffix = Path(str(telegram_file_path)).suffix
    if suffix:
        return suffix
    if attachment.mime_type:
        guessed = mimetypes.guess_extension(attachment.mime_type)
        if guessed:
            return ".jpg" if guessed == ".jpe" else guessed
    return _DEFAULT_EXTENSIONS.get(attachment.kind, ".bin")


def _ensure_size_allowed(file_size: int | None, max_bytes: int) -> None:
    if file_size is not None and file_size > max_bytes:
        raise AttachmentError(
            "Файл слишком большой для скачивания через Telegram Bot API.\n\n"
            f"Размер: {file_size} байт.\n"
            f"Лимит: {max_bytes} байт."
        )


def _dedupe_destination(path: Path) -> Path:
    if not path.exists():
        return path
    suffix = path.suffix
    stem = path.stem
    for index in range(2, 1000):
        candidate = path.with_name(f"{stem}-{index}{suffix}")
        if not candidate.exists():
            return candidate
    raise AttachmentError(f"Не удалось подобрать имя файла для {path.name}.")


def _is_text_like(path: Path, mime_type: str | None) -> bool:
    if path.suffix.lower() in _TEXT_EXTENSIONS:
        return True
    return bool(mime_type and mime_type.lower().startswith("text/"))


def _prompt_path(path: Path) -> str:
    return path.resolve().as_posix()
