"""OCR 결과를 Discord Embed용 텍스트로 정리."""

from __future__ import annotations

import re

DEFAULT_MAX_LENGTH = 3500


def _normalize_whitespace(text: str) -> str:
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    return "\n".join(lines)


def _to_bullets(text: str) -> str:
    lines = text.splitlines()
    if len(lines) <= 1:
        return text

    bullet_lines = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(("-", "•", "*", "·")):
            bullet_lines.append(stripped)
        else:
            bullet_lines.append(f"• {stripped}")
    return "\n".join(bullet_lines)


def format_for_discord(raw_text: str, max_length: int = DEFAULT_MAX_LENGTH) -> str:
    text = _normalize_whitespace(raw_text)
    if not text:
        return "_(OCR로 텍스트를 추출하지 못했습니다. 아래 이미지를 확인해 주세요.)_"

    text = _to_bullets(text)
    if len(text) <= max_length:
        return text

    truncated = text[: max_length - 1].rstrip()
    return f"{truncated}…"
