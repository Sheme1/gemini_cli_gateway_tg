from __future__ import annotations

import html
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class TelegramHtml:
    plain_text: str
    html_text: str

    @property
    def changed(self) -> bool:
        return self.html_text != self.plain_text


_THOUGHT_MARKER_RE = re.compile(r"\[Thought:\s*(?:true|false)\]\s*", re.IGNORECASE)
_NUMBERED_ITEM_RE = re.compile(r"(?<![\n\s])(\d+[.)]\s+)")
_BULLET_ITEM_RE = re.compile(r"(?<!\n)(?<=[.!?:])([*-]\s+)")
_HEADING_JOIN_RE = re.compile(r"(?<!\n)(?<=\S)(#{1,6}\s+)")
_BOLD_JOIN_RE = re.compile(r"(?<!\n)(?<=\S)(\*\*[^*\n]+:\*\*)")
_PUNCT_SPACE_RE = re.compile(r"([,;!])(?=[^\s\d])")
_SENTENCE_SPACE_RE = re.compile(r"(?<=[а-яА-ЯёЁ])\.(?=[а-яА-ЯёЁ])")
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_BOLD_RE = re.compile(r"\*\*([^*\n]+)\*\*")
_STRONG_RE = re.compile(r"__([^_\n]+)__")
_ITALIC_RE = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")


def render_telegram_html(text: str) -> TelegramHtml:
    """Render model Markdown-ish text to safe Telegram HTML."""
    plain_text = normalize_telegram_text(text)
    html_text = _render_blocks(plain_text)
    return TelegramHtml(plain_text=plain_text, html_text=html_text)


def normalize_telegram_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _THOUGHT_MARKER_RE.sub("", text)

    chunks = re.split(r"(```[\s\S]*?```)", text)
    normalized: list[str] = []
    for chunk in chunks:
        if chunk.startswith("```") and chunk.endswith("```"):
            normalized.append(chunk)
            continue
        normalized.append(_normalize_prose(chunk))

    return re.sub(r"\n{3,}", "\n\n", "".join(normalized)).strip()


def _normalize_prose(text: str) -> str:
    text = _NUMBERED_ITEM_RE.sub(r"\n\1", text)
    text = _BULLET_ITEM_RE.sub(r"\n\1", text)
    text = _HEADING_JOIN_RE.sub(r"\n\n\1", text)
    text = _BOLD_JOIN_RE.sub(r"\n\n\1", text)
    text = _PUNCT_SPACE_RE.sub(r"\1 ", text)
    text = _SENTENCE_SPACE_RE.sub(". ", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    return text


def _render_blocks(text: str) -> str:
    lines = text.split("\n")
    rendered: list[str] = []
    code_lines: list[str] = []
    in_code = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            if in_code:
                rendered.append(_render_code_block("\n".join(code_lines)))
                code_lines = []
                in_code = False
            else:
                if rendered and rendered[-1] != "":
                    rendered.append("")
                in_code = True
            continue

        if in_code:
            code_lines.append(line)
            continue

        rendered.append(_render_line(line))

    if in_code:
        rendered.append(_render_code_block("\n".join(code_lines)))

    html_text = "\n".join(rendered)
    html_text = re.sub(r"\n{3,}", "\n\n", html_text)
    return html_text.strip()


def _render_code_block(code: str) -> str:
    return f"<pre><code>{html.escape(code, quote=False)}</code></pre>"


def _render_line(line: str) -> str:
    stripped = line.strip()
    if not stripped:
        return ""

    heading = re.match(r"^(#{1,6})\s+(.+)$", stripped)
    if heading:
        return f"<b>{_render_inline(heading.group(2))}</b>"

    bullet = re.match(r"^[-*+]\s+(.+)$", stripped)
    if bullet:
        return f"• {_render_inline(bullet.group(1))}"

    numbered = re.match(r"^(\d+[.)])\s+(.+)$", stripped)
    if numbered:
        return f"{html.escape(numbered.group(1), quote=False)} {_render_inline(numbered.group(2))}"

    quote = re.match(r"^>\s?(.+)$", stripped)
    if quote:
        return f"<blockquote>{_render_inline(quote.group(1))}</blockquote>"

    return _render_inline(line)


def _render_inline(text: str) -> str:
    code_tokens: list[str] = []

    def replace_code(match: re.Match[str]) -> str:
        code_tokens.append(f"<code>{html.escape(match.group(1), quote=False)}</code>")
        return f"\x00CODE{len(code_tokens) - 1}\x00"

    text = _INLINE_CODE_RE.sub(replace_code, text)
    rendered = html.escape(text, quote=False)
    rendered = _BOLD_RE.sub(r"<b>\1</b>", rendered)
    rendered = _STRONG_RE.sub(r"<b>\1</b>", rendered)
    rendered = _ITALIC_RE.sub(r"<i>\1</i>", rendered)

    for index, token in enumerate(code_tokens):
        rendered = rendered.replace(f"\x00CODE{index}\x00", token)

    return rendered
