"""썬데이 메이플 알림 봇 진입점."""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone

from src.discord_notifier import DiscordNotifierError, send_success_embed
from src.gpt_analyzer import VisionAnalyzerError, analyze_event_image
from src.image_preprocess import prepare_discord_attachments
from src.scraper import ScraperError, create_session, download_image, fetch_sunday_maple_event
from src.state import (
    DEFAULT_STATE_PATH,
    NotificationState,
    already_notified,
    current_week_key,
    load_state,
    save_state,
)
from src.summarizer import format_for_discord

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("sunday_maple_alarm")


def _is_force_notify() -> bool:
    return os.getenv("FORCE_NOTIFY", "").lower() in ("1", "true", "yes")


def main() -> int:
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        logger.error("환경 변수 DISCORD_WEBHOOK_URL이 설정되지 않았습니다.")
        return 1

    if not os.getenv("OPENAI_API_KEY"):
        logger.error("환경 변수 OPENAI_API_KEY가 설정되지 않았습니다.")
        return 1

    force_notify = _is_force_notify()
    if force_notify:
        logger.info("FORCE_NOTIFY 활성화 — 중복 방지를 건너뜁니다 (테스트 모드).")

    week_key = current_week_key()
    state = load_state(DEFAULT_STATE_PATH)

    if not force_notify and state and already_notified(state, week_key, state.event_id):
        logger.info(
            "이번 주(%s) 알림이 이미 전송되었습니다 (event_id=%s). 조용히 종료합니다.",
            week_key,
            state.event_id,
        )
        return 0

    session = create_session()

    try:
        event = fetch_sunday_maple_event(session)
    except ScraperError as exc:
        logger.error("스크래핑 중 오류: %s", exc)
        return 1

    if event is None:
        logger.info(
            "이벤트 목록에 '%s' 키워드 게시글이 없습니다. 공지 미등록 — 조용히 종료합니다.",
            "썬데이 메이플",
        )
        return 0

    if not force_notify and already_notified(state, week_key, event.event_id):
        logger.info(
            "event_id=%s 알림이 이미 전송되었습니다. 조용히 종료합니다.",
            event.event_id,
        )
        return 0

    if not event.image_url:
        logger.error("이벤트 본문 이미지 URL을 찾지 못했습니다 (event_id=%s).", event.event_id)
        return 1

    try:
        image_bytes = download_image(session, event.image_url)
        image_attachments = prepare_discord_attachments(image_bytes)
        raw_text = analyze_event_image(
            image_bytes,
            title=event.title,
            period=event.period,
        )
        description = format_for_discord(raw_text)
        send_success_embed(
            webhook_url,
            title=event.title,
            period=event.period,
            description=description,
            detail_url=event.detail_url,
            image_url=event.image_url,
            image_attachments=image_attachments,
        )
    except (ScraperError, DiscordNotifierError, VisionAnalyzerError) as exc:
        logger.error("처리 중 오류: %s", exc)
        return 1
    except Exception as exc:
        logger.exception("예기치 않은 오류: %s", exc)
        return 1

    save_state(
        NotificationState(
            week_key=week_key,
            event_id=event.event_id,
            sent_at=datetime.now(timezone.utc).isoformat(),
        )
    )
    logger.info("썬데이 메이플 알림 작업 완료 (event_id=%s)", event.event_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
