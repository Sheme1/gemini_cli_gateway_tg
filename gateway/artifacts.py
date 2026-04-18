from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Iterable

from gateway.config import Config
from gateway.gemini.parser import StreamEvent

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from aiogram import Bot

_AUTO_SEND_EXTENSIONS = {
    ".csv",
    ".doc",
    ".docx",
    ".gif",
    ".jpeg",
    ".jpg",
    ".ods",
    ".odt",
    ".pdf",
    ".png",
    ".ppt",
    ".pptx",
    ".webp",
    ".xls",
    ".xlsx",
    ".zip",
}
_BLOCKED_AUTO_FILENAMES = {
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
}
_SKIP_DIRS = {
    ".git",
    ".gemini",
    ".gateway_state",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "node_modules",
}


class ArtifactManager:
    """Собирает кандидатов на отправку и находит артефакты после завершения запроса."""

    def __init__(self, config: Config):
        self.working_dir = Path(config.gemini_working_dir).expanduser().resolve()
        self.roots = [
            Path(root).expanduser().resolve() for root in config.gemini_artifact_roots
        ]
        self.stable_seconds = config.artifact_stable_seconds
        self._explicit_candidates: list[str] = []
        self._inferred_candidates: list[str] = []
        self._sent_paths: set[Path] = set()
        self._stability_state: dict[Path, tuple[int, int, float]] = {}

    def register_event(self, event: StreamEvent) -> None:
        if event.direct_file_candidates:
            self._extend_unique(self._explicit_candidates, event.direct_file_candidates)
        if event.file_candidates:
            self._extend_unique(self._inferred_candidates, event.file_candidates)

    @property
    def has_sent_artifacts(self) -> bool:
        return bool(self._sent_paths)

    async def send_artifacts(
        self,
        bot: "Bot",
        chat_id: int,
        started_at: float,
    ) -> list[Path]:
        explicit_paths = self._resolve_candidates(
            self._explicit_candidates,
            allow_non_deliverable=True,
        )
        inferred_paths = self._resolve_candidates(self._inferred_candidates)
        if explicit_paths:
            ordered_paths = explicit_paths
        elif inferred_paths:
            ordered_paths = inferred_paths
        else:
            ordered_paths = self._scan_recent_files(started_at)
        sent_paths: list[Path] = []

        for path in ordered_paths:
            if path in self._sent_paths:
                continue
            if await self._send_document(bot, chat_id, path):
                self._sent_paths.add(path)
                sent_paths.append(path)

        missing_explicit = [
            candidate
            for candidate in self._explicit_candidates
            if not any(path.name == Path(candidate).name for path in explicit_paths)
        ]
        if missing_explicit and not sent_paths:
            await bot.send_message(
                chat_id=chat_id,
                text=(
                    "⚠️ Файл был заявлен как созданный, но найти его не удалось.\n"
                    + "\n".join(f"• {candidate}" for candidate in missing_explicit[:5])
                ),
            )

        return sent_paths

    async def send_ready_artifacts(
        self,
        bot: "Bot",
        chat_id: int,
        started_at: float,
        now: float | None = None,
    ) -> list[Path]:
        ready_paths = self._ready_artifacts(started_at, now=now)
        sent_paths: list[Path] = []
        for path in ready_paths:
            if path in self._sent_paths:
                continue
            logger.info("Artifact stabilized and ready to send: %s", path)
            if await self._send_document(bot, chat_id, path):
                self._sent_paths.add(path)
                sent_paths.append(path)
        return sent_paths

    async def _send_document(self, bot: "Bot", chat_id: int, path: Path) -> bool:
        from aiogram.types import BufferedInputFile, FSInputFile

        caption = f"📎 Сгенерированный файл: {path.name}"
        try:
            await bot.send_document(
                chat_id=chat_id,
                document=FSInputFile(str(path), filename=path.name),
                caption=caption,
            )
            logger.info("Sent artifact via FSInputFile: %s", path)
            return True
        except Exception as first_error:
            logger.warning("FSInputFile upload failed for %s: %s", path, first_error)

        try:
            data = path.read_bytes()
            await bot.send_document(
                chat_id=chat_id,
                document=BufferedInputFile(data, filename=path.name),
                caption=caption,
            )
            logger.info("Sent artifact via BufferedInputFile: %s", path)
            return True
        except Exception as second_error:
            logger.error("BufferedInputFile upload failed for %s: %s", path, second_error)
            await bot.send_message(
                chat_id=chat_id,
                text=(
                    f"⚠️ Не удалось отправить файл {path.name}.\n"
                    f"Причина: {second_error}"
                ),
            )
            return False

    def _ready_artifacts(
        self,
        started_at: float,
        now: float | None = None,
    ) -> list[Path]:
        checked_at = time.time() if now is None else now
        explicit_paths = self._resolve_candidates(
            self._explicit_candidates,
            allow_non_deliverable=True,
        )
        inferred_paths = self._resolve_candidates(self._inferred_candidates)

        if explicit_paths:
            candidates = explicit_paths
        elif inferred_paths:
            candidates = inferred_paths
        else:
            candidates = self._scan_recent_files(started_at)

        ready_paths: list[Path] = []
        for path in candidates:
            if path in self._sent_paths:
                continue
            if self._is_stable(path, checked_at):
                ready_paths.append(path)
        return ready_paths

    def _resolve_candidates(
        self,
        candidates: Iterable[str],
        *,
        allow_non_deliverable: bool = False,
    ) -> list[Path]:
        resolved: list[Path] = []
        for candidate in candidates:
            path = self._resolve_candidate(
                candidate,
                allow_non_deliverable=allow_non_deliverable,
            )
            if path:
                resolved.append(path)
        return self._dedupe_paths(resolved)

    def _resolve_candidate(
        self,
        candidate: str,
        *,
        allow_non_deliverable: bool = False,
    ) -> Path | None:
        raw_path = Path(candidate).expanduser()
        search_paths: list[Path] = []

        if raw_path.is_absolute():
            search_paths.append(raw_path)
        else:
            search_paths.append(self.working_dir / raw_path)
            search_paths.extend(root / raw_path for root in self.roots)

        for search_path in search_paths:
            try:
                resolved = search_path.resolve()
            except OSError:
                continue
            if (
                resolved.exists()
                and resolved.is_file()
                and self._is_inside_roots(resolved)
                and (allow_non_deliverable or self._is_auto_send_deliverable(resolved))
            ):
                return resolved
        return None

    def _scan_recent_files(self, started_at: float, limit: int = 5) -> list[Path]:
        candidates: list[tuple[float, Path]] = []
        threshold = started_at - 2

        for root in self.roots:
            if not root.exists():
                continue
            for current_root, dirnames, filenames in os.walk(root):
                dirnames[:] = [name for name in dirnames if name not in _SKIP_DIRS]
                current_path = Path(current_root)
                for filename in filenames:
                    path = current_path / filename
                    try:
                        stat = path.stat()
                    except OSError:
                        continue
                    if (
                        stat.st_mtime < threshold
                        or not path.is_file()
                        or not self._is_auto_send_deliverable(path)
                    ):
                        continue
                    candidates.append((stat.st_mtime, path.resolve()))

        candidates.sort(key=lambda item: item[0], reverse=True)
        return self._dedupe_paths([path for _, path in candidates[:limit]])

    def _is_stable(self, path: Path, now: float) -> bool:
        try:
            stat = path.stat()
        except OSError:
            self._stability_state.pop(path, None)
            return False

        size = int(stat.st_size)
        mtime_ns = int(stat.st_mtime_ns)
        previous = self._stability_state.get(path)

        if previous is None or previous[:2] != (size, mtime_ns):
            self._stability_state[path] = (size, mtime_ns, now)
            return False

        stable_since = previous[2]
        if now - stable_since >= self.stable_seconds:
            return True
        return False

    def _is_inside_roots(self, path: Path) -> bool:
        for root in self.roots:
            try:
                path.relative_to(root)
                return True
            except ValueError:
                continue
        return False

    @staticmethod
    def _is_auto_send_deliverable(path: Path) -> bool:
        lowered_name = path.name.lower()
        if lowered_name in _BLOCKED_AUTO_FILENAMES:
            return False
        return path.suffix.lower() in _AUTO_SEND_EXTENSIONS

    @staticmethod
    def _extend_unique(target: list[str], values: Iterable[str]) -> None:
        for value in values:
            if value and value not in target:
                target.append(value)

    @staticmethod
    def _dedupe_paths(paths: Iterable[Path]) -> list[Path]:
        deduped: list[Path] = []
        seen: set[Path] = set()
        for path in paths:
            if path in seen:
                continue
            seen.add(path)
            deduped.append(path)
        return deduped
