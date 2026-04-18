from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Iterable

from gateway.config import Config
from gateway.gemini.parser import StreamEvent

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from aiogram import Bot

_SKIP_DIRS = {
    ".git",
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
        self._explicit_candidates: list[str] = []
        self._inferred_candidates: list[str] = []
        self._sent_paths: set[Path] = set()

    def register_event(self, event: StreamEvent) -> None:
        if event.direct_file_candidates:
            self._extend_unique(self._explicit_candidates, event.direct_file_candidates)
        if event.file_candidates:
            self._extend_unique(self._inferred_candidates, event.file_candidates)

    async def send_artifacts(
        self,
        bot: "Bot",
        chat_id: int,
        started_at: float,
    ) -> list[Path]:
        explicit_paths = self._resolve_candidates(self._explicit_candidates)
        inferred_paths = self._resolve_candidates(self._inferred_candidates)
        fallback_paths = self._scan_recent_files(started_at)

        ordered_paths = self._dedupe_paths(
            [*explicit_paths, *inferred_paths, *fallback_paths]
        )
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

    def _resolve_candidates(self, candidates: Iterable[str]) -> list[Path]:
        resolved: list[Path] = []
        for candidate in candidates:
            path = self._resolve_candidate(candidate)
            if path:
                resolved.append(path)
        return self._dedupe_paths(resolved)

    def _resolve_candidate(self, candidate: str) -> Path | None:
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
            if resolved.exists() and resolved.is_file() and self._is_inside_roots(resolved):
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
                    if stat.st_mtime < threshold or not path.is_file():
                        continue
                    candidates.append((stat.st_mtime, path.resolve()))

        candidates.sort(key=lambda item: item[0], reverse=True)
        return self._dedupe_paths([path for _, path in candidates[:limit]])

    def _is_inside_roots(self, path: Path) -> bool:
        for root in self.roots:
            try:
                path.relative_to(root)
                return True
            except ValueError:
                continue
        return False

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
