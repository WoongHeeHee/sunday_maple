"""Gemini Vision API로 이벤트 이미지 분석."""

from __future__ import annotations

import logging
import os
import time

import google.generativeai as genai
from google.api_core import exceptions as google_exceptions
from PIL import Image

from src.image_preprocess import FALLBACK_MAX_SIDE, MAX_IMAGE_SIDE, prepare_for_gemini

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-2.0-flash-lite"
MAX_RETRIES = 3
RETRY_BASE_DELAY = 8.0

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

CHUNK_PROMPT = """당신은 메이플스토리 '썬데이 메이플' 공지 이미지의 일부 구간을 분석합니다.
이 구간({chunk_index}/{chunk_total})에 보이는 혜택·조건·주의사항만 bullet list(•)로 추출하세요.
보이지 않는 내용은 추측하지 마세요.

이벤트 제목: {title}
이벤트 기간: {period}
"""

MERGE_PROMPT = """아래는 같은 썬데이 메이플 공지 이미지를 구간별로 분석한 결과입니다.
중복을 제거하고, Discord 메시지용 bullet list(•) 하나로 통합해 주세요.
불필요한 서론 없이 혜택 내용만 간결하게 정리하세요.

{combined}
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


def _is_rate_limit_error(exc: Exception) -> bool:
    if isinstance(exc, google_exceptions.ResourceExhausted):
        return True
    message = str(exc).lower()
    return "429" in message or "quota" in message or "rate" in message


def _generate_text(model: genai.GenerativeModel, parts: list) -> str:
    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = model.generate_content(parts)
            text = (response.text or "").strip()
            if not text:
                raise GeminiAnalyzerError("Gemini가 빈 응답을 반환했습니다.")
            return text
        except GeminiAnalyzerError:
            raise
        except Exception as exc:
            last_error = exc
            if _is_rate_limit_error(exc) and attempt < MAX_RETRIES:
                delay = RETRY_BASE_DELAY * attempt
                logger.warning(
                    "Gemini rate limit (시도 %d/%d), %ss 후 재시도: %s",
                    attempt,
                    MAX_RETRIES,
                    delay,
                    exc,
                )
                time.sleep(delay)
                continue
            break

    raise GeminiAnalyzerError(f"Gemini API 호출 실패: {last_error}") from last_error


def _analyze_single_image(
    model: genai.GenerativeModel,
    image: Image.Image,
    *,
    title: str,
    period: str,
) -> str:
    prompt = ANALYSIS_PROMPT.format(title=title or "썬데이 메이플", period=period or "미상")
    return _generate_text(model, [prompt, image])


def _analyze_chunks(
    model: genai.GenerativeModel,
    chunks: list[Image.Image],
    *,
    title: str,
    period: str,
) -> str:
    partial_results: list[str] = []
    total = len(chunks)

    for index, chunk in enumerate(chunks, start=1):
        prompt = CHUNK_PROMPT.format(
            chunk_index=index,
            chunk_total=total,
            title=title or "썬데이 메이플",
            period=period or "미상",
        )
        text = _generate_text(model, [prompt, chunk])
        partial_results.append(f"[구간 {index}/{total}]\n{text}")
        logger.info("구간 %d/%d 분석 완료", index, total)

    combined = "\n\n".join(partial_results)
    if total == 1:
        return partial_results[0].split("\n", 1)[-1].strip()

    return _generate_text(model, [MERGE_PROMPT.format(combined=combined)])


def analyze_event_image(
    image_bytes: bytes,
    *,
    title: str,
    period: str,
) -> str:
    model = _get_model()
    max_side = int(os.getenv("GEMINI_MAX_IMAGE_SIDE", str(MAX_IMAGE_SIDE)))

    try:
        chunks, _ = prepare_for_gemini(image_bytes, max_side=max_side)
        if len(chunks) == 1:
            text = _analyze_single_image(model, chunks[0], title=title, period=period)
        else:
            text = _analyze_chunks(model, chunks, title=title, period=period)
        logger.info("Gemini 이미지 분석 완료 (글자 수: %d)", len(text))
        return text
    except GeminiAnalyzerError as exc:
        if not _is_rate_limit_error(exc) or max_side <= FALLBACK_MAX_SIDE:
            raise

        logger.warning(
            "rate limit 지속 — 이미지를 더 축소해 재시도합니다 (max_side=%d).",
            FALLBACK_MAX_SIDE,
        )
        chunks, _ = prepare_for_gemini(image_bytes, max_side=FALLBACK_MAX_SIDE)
        if len(chunks) == 1:
            return _analyze_single_image(model, chunks[0], title=title, period=period)
        return _analyze_chunks(model, chunks, title=title, period=period)
