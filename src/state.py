"""주간 알림 중복 방지를 위한 로컬 상태 파일 관리."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
DEFAULT_STATE_PATH = Path("data/state.json")


@dataclass
class NotificationState:
    week_key: str
    event_id: str
    sent_at: str


def current_week_key(now: datetime | None = None) -> str:
    moment = now or datetime.now(KST)
    iso = moment.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def load_state(path: Path = DEFAULT_STATE_PATH) -> NotificationState | None:
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return NotificationState(
            week_key=raw["week_key"],
            event_id=str(raw["event_id"]),
            sent_at=raw["sent_at"],
        )
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        logger.warning("상태 파일을 읽지 못했습니다 (%s). 새로 시작합니다.", exc)
        return None


def save_state(state: NotificationState, path: Path = DEFAULT_STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(asdict(state), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info(
        "알림 상태 저장 완료: week=%s, event_id=%s",
        state.week_key,
        state.event_id,
    )


def already_notified(
    state: NotificationState | None,
    week_key: str,
    event_id: str,
) -> bool:
    if state is None:
        return False
    return state.week_key == week_key and state.event_id == event_id
