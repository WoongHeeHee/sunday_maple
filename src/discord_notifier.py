"""Discord Webhook Embed 전송."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

import requests

logger = logging.getLogger(__name__)

EMBED_COLOR = 0xFF6B35
WEBHOOK_TIMEOUT = 30
MAX_RETRIES = 2


class DiscordNotifierError(Exception):
    """Discord Webhook 전송 실패."""


def _post_webhook(webhook_url: str, payload: dict) -> None:
    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
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


def send_success_embed(
    webhook_url: str,
    *,
    title: str,
    period: str,
    description: str,
    detail_url: str,
    image_url: str | None,
) -> None:
    embed_title = f"🍁 {title}"
    if period:
        embed_title = f"🍁 {title} — {period}"

    embed = {
        "title": embed_title[:256],
        "description": description[:4096],
        "url": detail_url,
        "color": EMBED_COLOR,
        "footer": {"text": "Sunday Maple Alarm Bot"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if image_url:
        embed["image"] = {"url": image_url}

    payload = {"embeds": [embed]}
    _post_webhook(webhook_url, payload)
    logger.info("Discord 알림 전송 완료")
