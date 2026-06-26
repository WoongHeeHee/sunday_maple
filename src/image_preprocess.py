"""Vision API 전송 전 이벤트 이미지 전처리."""

from __future__ import annotations

import io
import logging

from PIL import Image

logger = logging.getLogger(__name__)

# 긴 변 기준 리사이즈 — 토큰 사용량을 예측 가능한 수준으로 제한
MAX_IMAGE_SIDE = 1280
# 리사이즈 후에도 세로가 길면 구간 분할
MAX_CHUNK_HEIGHT = 1400
# 429 재시도 시 더 작게 줄이는 한도
FALLBACK_MAX_SIDE = 768
# Discord 첨부용 세로 분할 높이 (원본 가로 해상도 유지)
DISCORD_CHUNK_HEIGHT = 1100


def _resize_by_longest_side(image: Image.Image, max_side: int) -> Image.Image:
    width, height = image.size
    longest = max(width, height)
    if longest <= max_side:
        return image

    scale = max_side / longest
    new_size = (max(1, int(width * scale)), max(1, int(height * scale)))
    return image.resize(new_size, Image.Resampling.LANCZOS)


def _to_rgb(image: Image.Image) -> Image.Image:
    if image.mode == "RGB":
        return image
    if image.mode == "RGBA":
        background = Image.new("RGB", image.size, (255, 255, 255))
        background.paste(image, mask=image.split()[3])
        return background
    return image.convert("RGB")


def split_tall_image(image: Image.Image, chunk_height: int = MAX_CHUNK_HEIGHT) -> list[Image.Image]:
    if image.height <= chunk_height:
        return [image]

    chunks: list[Image.Image] = []
    y = 0
    while y < image.height:
        bottom = min(y + chunk_height, image.height)
        chunks.append(image.crop((0, y, image.width, bottom)))
        y = bottom
    return chunks


def prepare_event_image(
    image_bytes: bytes,
    *,
    max_side: int = MAX_IMAGE_SIDE,
) -> tuple[list[Image.Image], dict[str, int | str]]:
    """이미지를 리사이즈하고 필요 시 세로 구간으로 분할합니다."""
    original = Image.open(io.BytesIO(image_bytes))
    original_size = original.size

    rgb = _to_rgb(original)
    resized = _resize_by_longest_side(rgb, max_side)
    chunks = split_tall_image(resized)

    meta = {
        "original_width": original_size[0],
        "original_height": original_size[1],
        "processed_width": resized.size[0],
        "processed_height": resized.size[1],
        "chunk_count": len(chunks),
        "max_side": max_side,
    }
    logger.info(
        "이미지 전처리: %dx%d -> %dx%d, chunks=%d (max_side=%d)",
        meta["original_width"],
        meta["original_height"],
        meta["processed_width"],
        meta["processed_height"],
        meta["chunk_count"],
        max_side,
    )
    return chunks, meta


def _image_to_png_bytes(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def prepare_discord_attachments(
    image_bytes: bytes,
    *,
    chunk_height: int = DISCORD_CHUNK_HEIGHT,
) -> list[tuple[str, bytes]]:
    """Discord 첨부용으로 원본 해상도 기준 세로 분할 PNG를 생성합니다."""
    original = Image.open(io.BytesIO(image_bytes))
    original_size = original.size
    rgb = _to_rgb(original)
    chunks = split_tall_image(rgb, chunk_height=chunk_height)

    attachments: list[tuple[str, bytes]] = []
    total = len(chunks)
    for index, chunk in enumerate(chunks, start=1):
        filename = f"sunday_maple_{index}_of_{total}.png"
        attachments.append((filename, _image_to_png_bytes(chunk)))

    logger.info(
        "Discord 이미지 분할: %dx%d -> %d개 첨부 (chunk_height=%d)",
        original_size[0],
        original_size[1],
        total,
        chunk_height,
    )
    return attachments
