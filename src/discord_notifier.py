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

_MIME_BY_EXTENSION = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


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


def _mime_type_for_filename(filename: str) -> str:
    extension = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return _MIME_BY_EXTENSION.get(extension, "image/png")


def send_success_embed(
    webhook_url: str,
    *,
    title: str,
    period: str,
    description: str,
    detail_url: str,
    image_url: str | None,
    image_attachment: tuple[str, bytes] | None = None,
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

    if image_attachment:
        filename, data = image_attachment
        mime = _mime_type_for_filename(filename)
        payload = {"embeds": [main_embed]}
        files = [("files[0]", (filename, data, mime))]
        _post_webhook(webhook_url, payload, files=files)
        logger.info("Discord 알림 전송 완료 (원본 이미지 첨부)")
        return

    if image_url:
        main_embed["image"] = {"url": image_url}

    _post_webhook(webhook_url, {"embeds": [main_embed]})
    logger.info("Discord 알림 전송 완료")
