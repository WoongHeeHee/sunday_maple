"""OpenAI GPT Vision API로 이벤트 이미지 분석."""

from __future__ import annotations

import base64
import io
import logging
import os
import time

from openai import APIStatusError, OpenAI, RateLimitError
from PIL import Image

from src.image_preprocess import FALLBACK_MAX_SIDE, MAX_IMAGE_SIDE, prepare_event_image

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gpt-4o-mini"
MAX_RETRIES = 3
RETRY_BASE_DELAY = 8.0
DEFAULT_IMAGE_DETAIL = "low"
FALLBACK_IMAGE_DETAIL = "low"

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


class VisionAnalyzerError(Exception):
    """Vision API 이미지 분석 실패."""


def _get_client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise VisionAnalyzerError("환경 변수 OPENAI_API_KEY가 설정되지 않았습니다.")
    return OpenAI(api_key=api_key)


def _get_model() -> str:
    return os.getenv("OPENAI_MODEL", DEFAULT_MODEL)


def _is_rate_limit_error(exc: Exception) -> bool:
    if isinstance(exc, RateLimitError):
        return True
    if isinstance(exc, APIStatusError) and exc.status_code == 429:
        return True
    message = str(exc).lower()
    return "429" in message or "quota" in message or "rate limit" in message


def _image_to_data_url(image: Image.Image) -> str:
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=85)
    encoded = base64.b64encode(buffer.getvalue()).decode("utf-8")
    return f"data:image/jpeg;base64,{encoded}"


def _generate_text(
    client: OpenAI,
    *,
    prompt: str,
    image: Image.Image | None = None,
    image_detail: str = DEFAULT_IMAGE_DETAIL,
) -> str:
    if image is not None:
        content: list[dict] = [
            {"type": "text", "text": prompt},
            {
                "type": "image_url",
                "image_url": {
                    "url": _image_to_data_url(image),
                    "detail": image_detail,
                },
            },
        ]
    else:
        content = [{"type": "text", "text": prompt}]

    last_error: Exception | None = None
    model = _get_model()

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": content}],
                max_tokens=4096,
            )
            text = (response.choices[0].message.content or "").strip()
            if not text:
                raise VisionAnalyzerError("GPT가 빈 응답을 반환했습니다.")
            return text
        except VisionAnalyzerError:
            raise
        except Exception as exc:
            last_error = exc
            if _is_rate_limit_error(exc) and attempt < MAX_RETRIES:
                delay = RETRY_BASE_DELAY * attempt
                logger.warning(
                    "GPT rate limit (시도 %d/%d), %ss 후 재시도: %s",
                    attempt,
                    MAX_RETRIES,
                    delay,
                    exc,
                )
                time.sleep(delay)
                continue
            break

    raise VisionAnalyzerError(f"GPT API 호출 실패: {last_error}") from last_error


def _analyze_single_image(
    client: OpenAI,
    image: Image.Image,
    *,
    title: str,
    period: str,
    image_detail: str,
) -> str:
    prompt = ANALYSIS_PROMPT.format(title=title or "썬데이 메이플", period=period or "미상")
    return _generate_text(client, prompt=prompt, image=image, image_detail=image_detail)


def _analyze_chunks(
    client: OpenAI,
    chunks: list[Image.Image],
    *,
    title: str,
    period: str,
    image_detail: str,
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
        text = _generate_text(client, prompt=prompt, image=chunk, image_detail=image_detail)
        partial_results.append(f"[구간 {index}/{total}]\n{text}")
        logger.info("구간 %d/%d 분석 완료", index, total)

    combined = "\n\n".join(partial_results)
    if total == 1:
        return partial_results[0].split("\n", 1)[-1].strip()

    return _generate_text(client, prompt=MERGE_PROMPT.format(combined=combined))


def _run_analysis(
    client: OpenAI,
    image_bytes: bytes,
    *,
    title: str,
    period: str,
    max_side: int,
    image_detail: str,
) -> str:
    chunks, _ = prepare_event_image(image_bytes, max_side=max_side)
    if len(chunks) == 1:
        return _analyze_single_image(
            client,
            chunks[0],
            title=title,
            period=period,
            image_detail=image_detail,
        )
    return _analyze_chunks(
        client,
        chunks,
        title=title,
        period=period,
        image_detail=image_detail,
    )


def analyze_event_image(
    image_bytes: bytes,
    *,
    title: str,
    period: str,
) -> str:
    client = _get_client()
    max_side = int(os.getenv("OPENAI_MAX_IMAGE_SIDE", str(MAX_IMAGE_SIDE)))
    image_detail = os.getenv("OPENAI_IMAGE_DETAIL", DEFAULT_IMAGE_DETAIL)

    try:
        text = _run_analysis(
            client,
            image_bytes,
            title=title,
            period=period,
            max_side=max_side,
            image_detail=image_detail,
        )
        logger.info("GPT 이미지 분석 완료 (글자 수: %d)", len(text))
        return text
    except VisionAnalyzerError as exc:
        if not _is_rate_limit_error(exc) or max_side <= FALLBACK_MAX_SIDE:
            raise

        logger.warning(
            "rate limit 지속 — 이미지를 더 축소해 재시도합니다 (max_side=%d).",
            FALLBACK_MAX_SIDE,
        )
        return _run_analysis(
            client,
            image_bytes,
            title=title,
            period=period,
            max_side=FALLBACK_MAX_SIDE,
            image_detail=FALLBACK_IMAGE_DETAIL,
        )
