"""메이플스토리 이벤트 페이지 스크래핑."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

BASE_URL = "https://maplestory.nexon.com"
EVENT_LIST_URL = f"{BASE_URL}/News/Event"
EVENT_DETAIL_URL = f"{BASE_URL}/News/Event/Ongoing/{{event_id}}"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
SUNDAY_MAPLE_KEYWORD = "썬데이 메이플"
EVENT_ID_PATTERN = re.compile(r"/News/Event/(?:Ongoing/)?(\d+)")
MAIN_IMAGE_HOST = "lwi.nexon.com"

DEFAULT_TIMEOUT = 30
MAX_RETRIES = 3
RETRY_BACKOFF = 2.0


@dataclass(frozen=True)
class SundayMapleEvent:
    event_id: str
    title: str
    period: str
    detail_url: str
    image_url: str | None = None


class ScraperError(Exception):
    """스크래핑 관련 오류."""


def _request_with_retry(
    session: requests.Session,
    url: str,
    *,
    timeout: int = DEFAULT_TIMEOUT,
) -> requests.Response:
    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = session.get(url, timeout=timeout)
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_error = exc
            if attempt < MAX_RETRIES:
                wait = RETRY_BACKOFF ** (attempt - 1)
                logger.warning(
                    "요청 실패 (%s), %d/%d회 재시도 (%ss 후): %s",
                    url,
                    attempt,
                    MAX_RETRIES,
                    wait,
                    exc,
                )
                time.sleep(wait)
    raise ScraperError(f"요청 실패: {url}") from last_error


def fetch_html(session: requests.Session, url: str) -> BeautifulSoup:
    response = _request_with_retry(session, url)
    return BeautifulSoup(response.text, "lxml")


def _extract_event_id(href: str | None) -> str | None:
    if not href:
        return None
    match = EVENT_ID_PATTERN.search(href)
    return match.group(1) if match else None


def _is_sunday_maple_title(title: str) -> bool:
    return SUNDAY_MAPLE_KEYWORD in title


def find_latest_sunday_maple(soup: BeautifulSoup) -> SundayMapleEvent | None:
    banner = soup.select_one("ul.event_all_banner")
    if banner is None:
        logger.error("이벤트 목록(.event_all_banner)을 찾을 수 없습니다.")
        return None

    for item in banner.select("li"):
        title_link = item.select_one("dl dt a")
        if title_link is None:
            continue

        title = title_link.get_text(strip=True)
        if not _is_sunday_maple_title(title):
            continue

        event_id = _extract_event_id(title_link.get("href"))
        if event_id is None:
            continue

        period = ""
        period_link = item.select_one("dl dd a")
        if period_link:
            period = period_link.get_text(strip=True)

        detail_url = EVENT_DETAIL_URL.format(event_id=event_id)
        logger.info("썬데이 이벤트 발견: id=%s, title=%s", event_id, title)
        return SundayMapleEvent(
            event_id=event_id,
            title=title,
            period=period,
            detail_url=detail_url,
        )

    return None


def extract_detail_metadata(soup: BeautifulSoup) -> tuple[str, str]:
    title = ""
    title_node = soup.select_one("p.qs_title span")
    if title_node:
        title = title_node.get_text(strip=True)

    period = ""
    period_node = soup.select_one("span.event_date")
    if period_node:
        period = period_node.get_text(strip=True)

    return title or "", period or ""


def extract_main_image_url(soup: BeautifulSoup) -> str | None:
    content = soup.select_one(".new_board_con")
    if content is None:
        logger.error("본문 영역(.new_board_con)을 찾을 수 없습니다.")
        return None

    for img in content.find_all("img"):
        src = img.get("src") or img.get("data-src")
        if not src:
            continue
        absolute = urljoin(BASE_URL, src)
        if MAIN_IMAGE_HOST in absolute:
            return absolute

    logger.error("본문 이미지(lwi.nexon.com) URL을 찾을 수 없습니다.")
    return None


def download_image(session: requests.Session, url: str) -> bytes:
    response = _request_with_retry(session, url)
    if not response.content:
        raise ScraperError(f"이미지가 비어 있습니다: {url}")
    logger.info("이미지 다운로드 완료 (%d bytes)", len(response.content))
    return response.content


def create_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    return session


def fetch_sunday_maple_event(
    session: requests.Session | None = None,
) -> SundayMapleEvent | None:
    session = session or create_session()

    list_soup = fetch_html(session, EVENT_LIST_URL)
    event = find_latest_sunday_maple(list_soup)
    if event is None:
        return None

    detail_soup = fetch_html(session, event.detail_url)
    detail_title, detail_period = extract_detail_metadata(detail_soup)
    image_url = extract_main_image_url(detail_soup)

    return SundayMapleEvent(
        event_id=event.event_id,
        title=detail_title or event.title,
        period=detail_period or event.period,
        detail_url=event.detail_url,
        image_url=image_url,
    )
