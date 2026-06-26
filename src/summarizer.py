"""Gemini 분석 결과를 Discord Embed용 텍스트로 정리."""

from __future__ import annotations

import re

DEFAULT_MAX_LENGTH = 3500


def _normalize_whitespace(text: str) -> str:
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    return "\n".join(lines)


def format_for_discord(raw_text: str, max_length: int = DEFAULT_MAX_LENGTH) -> str:
    text = _normalize_whitespace(raw_text)
    if not text:
        return "_(이벤트 내용을 분석하지 못했습니다. 아래 이미지를 확인해 주세요.)_"

    if len(text) <= max_length:
        return text

    truncated = text[: max_length - 1].rstrip()
    return f"{truncated}…"
