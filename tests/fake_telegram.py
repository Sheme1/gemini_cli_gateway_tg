from collections.abc import Iterable
from types import SimpleNamespace
from typing import Any

from aiogram.exceptions import TelegramBadRequest


class FakeTelegramBot:
    def __init__(self, failures: Iterable[Exception] | None = None) -> None:
        self._next_message_id = 1
        self.failures = list(failures or [])
        self.sent: list[dict[str, Any]] = []
        self.edits: list[dict[str, Any]] = []
        self.messages: list[dict[str, Any]] = []
        self.documents: list[dict[str, Any]] = []

    def _new_message_id(self) -> int:
        message_id = self._next_message_id
        self._next_message_id += 1
        return message_id

    def _record_sent(self, message: dict[str, Any]) -> None:
        self.sent.append(message)
        self.messages.append(message)

    def _record_edit(self, edit: dict[str, Any]) -> None:
        self.edits.append(edit)
        self.messages.append(edit)

    def _raise_next_failure(self) -> None:
        if self.failures:
            raise self.failures.pop(0)

    async def send_message(self, chat_id: int, text: str, parse_mode=None):
        message = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "message_id": self._new_message_id(),
        }
        self._record_sent(message)
        return SimpleNamespace(message_id=message["message_id"])

    async def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        parse_mode=None,
    ) -> None:
        self._record_edit(
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
                "parse_mode": parse_mode,
            }
        )
        self._raise_next_failure()

    async def send_document(self, chat_id: int, document, caption: str | None = None):
        filename = getattr(document, "filename", None)
        self.documents.append(
            {"chat_id": chat_id, "filename": filename, "caption": caption}
        )
        return SimpleNamespace(document=filename)


class ReplyMarkupFakeTelegramBot(FakeTelegramBot):
    async def send_message(
        self,
        chat_id: int,
        text: str,
        reply_markup=None,
        parse_mode=None,
    ):
        message = {
            "chat_id": chat_id,
            "text": text,
            "reply_markup": reply_markup,
            "parse_mode": parse_mode,
            "message_id": self._new_message_id(),
        }
        self._record_sent(message)
        return SimpleNamespace(message_id=message["message_id"])

    async def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        reply_markup=None,
        parse_mode=None,
    ) -> None:
        self._record_edit(
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
                "reply_markup": reply_markup,
                "parse_mode": parse_mode,
                "edited": True,
            }
        )
        self._raise_next_failure()


class HtmlRejectingFakeTelegramBot(FakeTelegramBot):
    async def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        parse_mode=None,
    ) -> None:
        self._record_edit(
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
                "parse_mode": parse_mode,
            }
        )
        if parse_mode == "HTML":
            raise TelegramBadRequest(
                method=None,  # type: ignore[arg-type]
                message="Bad Request: can't parse entities",
            )
