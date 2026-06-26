"""이미지 OCR: Tesseract 1차, OCR.space 폴백."""

from __future__ import annotations

import io
import logging
import os

import pytesseract
import requests
from PIL import Image, ImageEnhance, ImageOps

logger = logging.getLogger(__name__)

OCR_SPACE_URL = "https://api.ocr.space/parse/image"
MIN_TEXT_LENGTH = 15
OCR_TIMEOUT = 60


class OcrError(Exception):
    """OCR 처리 실패."""


def preprocess_image(image_bytes: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(image_bytes))
    if image.mode != "RGB":
        image = image.convert("RGB")

    if image.width < 1200:
        scale = 1200 / image.width
        image = image.resize(
            (int(image.width * scale), int(image.height * scale)),
            Image.Resampling.LANCZOS,
        )

    image = ImageOps.grayscale(image)
    return ImageEnhance.Contrast(image).enhance(1.5)


def extract_text_tesseract(image_bytes: bytes) -> str:
    image = preprocess_image(image_bytes)
    text = pytesseract.image_to_string(image, lang="kor")
    cleaned = text.strip()
    logger.info("Tesseract OCR 완료 (글자 수: %d)", len(cleaned))
    return cleaned


def extract_text_ocr_space(image_bytes: bytes, api_key: str) -> str:
    if len(image_bytes) > 1024 * 1024:
        raise OcrError("OCR.space 무료 티어 파일 크기 제한(1MB)을 초과했습니다.")

    response = requests.post(
        OCR_SPACE_URL,
        files={"file": ("event.png", image_bytes, "image/png")},
        data={
            "apikey": api_key,
            "language": "kor",
            "OCREngine": "2",
            "isTable": "false",
        },
        timeout=OCR_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()

    if payload.get("IsErroredOnProcessing"):
        message = payload.get("ErrorMessage") or payload.get("ErrorDetails") or "unknown"
        raise OcrError(f"OCR.space 처리 오류: {message}")

    results = payload.get("ParsedResults") or []
    if not results:
        raise OcrError("OCR.space 결과가 비어 있습니다.")

    text = (results[0].get("ParsedText") or "").strip()
    logger.info("OCR.space OCR 완료 (글자 수: %d)", len(text))
    return text


def extract_text(image_bytes: bytes, ocr_space_api_key: str | None = None) -> str:
    tesseract_text = ""
    try:
        tesseract_text = extract_text_tesseract(image_bytes)
    except Exception as exc:
        logger.warning("Tesseract OCR 실패: %s", exc)

    if len(tesseract_text) >= MIN_TEXT_LENGTH:
        return tesseract_text

    api_key = ocr_space_api_key or os.getenv("OCR_SPACE_API_KEY")
    if not api_key:
        logger.warning(
            "OCR.space API 키가 없어 폴백을 건너뜁니다. Tesseract 결과만 사용합니다."
        )
        return tesseract_text

    try:
        fallback_text = extract_text_ocr_space(image_bytes, api_key)
        if len(fallback_text) >= len(tesseract_text):
            return fallback_text
    except Exception as exc:
        logger.warning("OCR.space 폴백 실패: %s", exc)

    return tesseract_text
