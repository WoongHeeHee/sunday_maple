"""Gemini Vision API로 이벤트 이미지 분석."""

from __future__ import annotations

import io
import logging
import os

import google.generativeai as genai
from PIL import Image

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-2.0-flash"
ANALYSIS_PROMPT = """당신은 메이플스토리 '썬데이 메이플' 이벤트 공지를 분석하는 도우미입니다.
첨부된 이벤트 이미지를 읽고, 이번 주 혜택 내용을 한국어로 정리해 주세요.

이벤트 제목: {title}
이벤트 기간: {period}

요구사항:
- 이미지에 있는 모든 주요 혜택, 조건, 주의사항을 빠짐없이 포함
- 도표·표·박스 등 복잡한 레이아웃도 내용을 풀어서 설명
- Discord 메시지에 적합하게 bullet list(•) 형식으로 작성
- 불필요한 서론·결론 없이 혜택 내용만 간결하게
- 이미지에 없는 내용은 추측하지 말 것
- 확률에 대한 수치 등이 많이 있을 경우 전부 텍스트로 변환하지 말 것 (지저분하게 정리하지 말 것)
"""


class GeminiAnalyzerError(Exception):
    """Gemini 이미지 분석 실패."""


def _get_model() -> genai.GenerativeModel:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise GeminiAnalyzerError("환경 변수 GEMINI_API_KEY가 설정되지 않았습니다.")

    genai.configure(api_key=api_key)
    model_name = os.getenv("GEMINI_MODEL", DEFAULT_MODEL)
    return genai.GenerativeModel(model_name)


def analyze_event_image(
    image_bytes: bytes,
    *,
    title: str,
    period: str,
) -> str:
    try:
        image = Image.open(io.BytesIO(image_bytes))
    except Exception as exc:
        raise GeminiAnalyzerError("이미지를 열 수 없습니다.") from exc

    prompt = ANALYSIS_PROMPT.format(title=title or "썬데이 메이플", period=period or "미상")
    model = _get_model()

    try:
        response = model.generate_content([prompt, image])
    except Exception as exc:
        raise GeminiAnalyzerError(f"Gemini API 호출 실패: {exc}") from exc

    text = (response.text or "").strip()
    if not text:
        raise GeminiAnalyzerError("Gemini가 빈 응답을 반환했습니다.")

    logger.info("Gemini 이미지 분석 완료 (글자 수: %d)", len(text))
    return text
