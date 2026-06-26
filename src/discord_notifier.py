"""Discord Webhook Embed 전송."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone

import requests

logger = logging.getLogger(__name__)

EMBED_COLOR = 0xFF6B35
WEBHOOK_TIMEOUT = 60
MAX_RETRIES = 2
MAX_ATTACHMENTS = 10


class DiscordNotifierError(Exception):
    """Discord Webhook 전송 실패."""


def _post_webhook(webhook_url: str, payload: dict, files: list | None = None) -> None:
    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            if files:
                response = requests.post(
                    webhook_url,
                    data={"payload_json": json.dumps(payload)},
                    files=files,
                    timeout=WEBHOOK_TIMEOUT,
                )
            else:
                response = requests.post(
                    webhook_url,
                    json=payload,
                    timeout=WEBHOOK_TIMEOUT,
                )
            if response.status_code == 429:
                retry_after = float(response.json().get("retry_after", 2))
                logger.warning("Discord rate limit, %ss 대기", retry_after)
                time.sleep(retry_after)
                continue
            response.raise_for_status()
            return
        except requests.RequestException as exc:
            last_error = exc
            if attempt < MAX_RETRIES:
                time.sleep(2)
    raise DiscordNotifierError("Discord Webhook 전송 실패") from last_error


def _build_multipart_files(attachments: list[tuple[str, bytes]]) -> list[tuple[str, tuple]]:
    return [
        (f"files[{index}]", (filename, data, "image/png"))
        for index, (filename, data) in enumerate(attachments)
    ]


def send_success_embed(
    webhook_url: str,
    *,
    title: str,
    period: str,
    description: str,
    detail_url: str,
    image_url: str | None,
    image_attachments: list[tuple[str, bytes]] | None = None,
) -> None:
    embed_title = f"🍁 {title}"
    if period:
        embed_title = f"🍁 {title} — {period}"

    main_embed: dict = {
        "title": embed_title[:256],
        "description": description[:4096],
        "url": detail_url,
        "color": EMBED_COLOR,
        "footer": {"text": "Sunday Maple Alarm Bot"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    if image_attachments:
        # embed.image에 넣지 않고 파일만 첨부하면 Discord가 갤러리 형태로 묶어 표시함
        if image_url:
            main_embed["description"] = (
                f"{description[:4000]}\n\n"
                f"🔗 [원본 이미지 보기]({image_url})"
            )[:4096]

        if len(image_attachments) > MAX_ATTACHMENTS:
            logger.warning(
                "Discord 첨부 제한으로 이미지 %d/%d개만 전송합니다.",
                MAX_ATTACHMENTS,
                len(image_attachments),
            )
            image_attachments = image_attachments[:MAX_ATTACHMENTS]

        payload = {"embeds": [main_embed]}
        files = _build_multipart_files(image_attachments)
        _post_webhook(webhook_url, payload, files=files)
        logger.info("Discord 알림 전송 완료 (이미지 %d개 갤러리 첨부)", len(image_attachments))
        return

    if image_url:
        main_embed["image"] = {"url": image_url}

    _post_webhook(webhook_url, {"embeds": [main_embed]})
    logger.info("Discord 알림 전송 완료")
