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

ANALYSIS_PROMPT = """
당신은 메이플스토리 '썬데이 메이플' 이벤트 이미지를 요약하는 도우미입니다.

첨부된 이미지를 읽고 Discord용으로 보기 쉽게 정리하세요.

이벤트 제목: {title}
이벤트 기간: {period}

출력 규칙:

[전체 구조]
반드시 아래 형식을 따를 것.

# 이번주(**YY.MM.DD**) 썬데이 메이플 이벤트

## [혜택명]
### - [핵심 내용]

## [혜택명]
### - [핵심 내용]

[출력 예시]
# 이번주(**26.06.21**) 썬데이 메이플 이벤트

## 트레저 헌터 경험치 **3배**
### - 하루 **10회**까지 발견 가능

## 룬 재등장 및 재사용 대기시간 감소
### - **15분 -> 10분**

## 룬 경험치 버프 효과 **+100%**
### - 기본 룬: **300%**
### - 축복의 룬: **400%**

[중요 분류 규칙]
- "##"는 실제 플레이어가 받는 독립 혜택에만 사용한다.
- 설명용 표, 확률표, 비교표, 예시표, 세부 수치표는 새로운 혜택으로 분리하지 않는다.
- 표/도표/확률표는 가장 가까운 상위 혜택에 귀속시켜 요약한다.
- 어떤 요소가 "혜택의 설명"인지 "독립 혜택"인지 먼저 판단한 후 구조화한다.

[작성 규칙]
- 날짜는 시작일만 사용 (종료일 제외)
- 날짜 형식은 YY.MM.DD
- 이벤트 기간(00:00 ~ 23:59)은 출력하지 않음
- 혜택마다 반드시 "##" 헤더 사용
- 세부 내용은 반드시 "### -" 형식 사용
- 핵심 수치(횟수, 시간, 배수, 퍼센트)는 **굵게**
- 변화 수치는 "A -> B" 형식 사용
- 여러 결과값이 있으면 각각 분리해서 작성
- 장황한 설명 제거
- 이미지 내 장식 문구 제거
- 중복 내용 제거
- 핵심 정보만 남기기

[요약 원칙]
- 한눈에 읽히도록 최대한 담백하게 작성
- 이벤트를 "설명"하지 말고 "혜택 목록"처럼 정리
- 표/박스 구조는 결과만 추출
- 계산 과정은 생략하고 최종 수치만 작성

[금지]
- 서론/결론 금지
- "다음과 같습니다" 금지
- 이미지에 없는 내용 추측 금지
- 불필요한 부가 설명 금지
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
