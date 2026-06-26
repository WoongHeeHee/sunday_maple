"""메이플스토리 이벤트 페이지 스크래핑."""

from __future__ import annotations

import logging
import os
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
JINA_PROXY_PREFIX = "https://r.jina.ai/"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
BROWSER_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

SUNDAY_MAPLE_KEYWORD = "썬데이 메이플"
EVENT_ID_PATTERN = re.compile(r"/News/Event/(?:Ongoing/)?(\d+)")
MAIN_IMAGE_HOST = "lwi.nexon.com"
BOARD_IMAGE_PATTERN = re.compile(
    r"https://lwi\.nexon\.com/maplestory/\d+/[0-9_a-zA-Z]+_board/[0-9A-Fa-f]+\.png"
)
JINA_EVENT_LINK_PATTERN = re.compile(
    r"\[[^\]]*?(썬데이 메이플)[^\]]*?\]"
    r"\(https://maplestory\.nexon\.com/News/Event/(?:Ongoing/)?(\d+)\)"
)
JINA_PERIOD_PATTERN = re.compile(r"(\d{4}\.\d{2}\.\d{2}\s*\([^)]+\)\s*~[^)\]]+)")

DEFAULT_TIMEOUT = (15, 45)
MAX_RETRIES = 4
RETRY_BACKOFF = 3.0


@dataclass(frozen=True)
class SundayMapleEvent:
    event_id: str
    title: str
    period: str
    detail_url: str
    image_url: str | None = None


class ScraperError(Exception):
    """스크래핑 관련 오류."""


def _is_connection_error(exc: Exception) -> bool:
    if isinstance(
        exc,
        (
            requests.exceptions.ConnectTimeout,
            requests.exceptions.ConnectionError,
            requests.exceptions.ReadTimeout,
        ),
    ):
        return True
    message = str(exc).lower()
    return "connect timeout" in message or "connection" in message or "timed out" in message


def _jina_headers() -> dict[str, str]:
    headers = {"Accept": "text/plain", "User-Agent": USER_AGENT}
    api_key = os.getenv("JINA_API_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _request_with_retry(
    session: requests.Session,
    url: str,
    *,
    timeout: tuple[int, int] | int = DEFAULT_TIMEOUT,
    headers: dict[str, str] | None = None,
) -> requests.Response:
    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = session.get(url, timeout=timeout, headers=headers)
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


def _fetch_direct(session: requests.Session, url: str) -> str:
    response = _request_with_retry(session, url, headers=BROWSER_HEADERS)
    return response.text


def _fetch_via_jina(session: requests.Session, url: str) -> str:
    proxy_url = f"{JINA_PROXY_PREFIX}{url}"
    logger.info("Jina 프록시로 페이지 요청: %s", url)
    response = _request_with_retry(
        session,
        proxy_url,
        headers=_jina_headers(),
    )
    return response.text


def fetch_page(session: requests.Session, url: str) -> tuple[str, str]:
    """페이지 본문과 소스 유형(html|jina)을 반환합니다."""
    try:
        return _fetch_direct(session, url), "html"
    except ScraperError as exc:
        cause = exc.__cause__
        if not isinstance(cause, Exception) or not _is_connection_error(cause):
            raise
        logger.warning("직접 접속 실패, Jina 프록시로 전환합니다: %s", url)
        try:
            return _fetch_via_jina(session, url), "jina"
        except ScraperError as jina_exc:
            raise ScraperError(
                f"넥슨 페이지 직접 접속 및 Jina 프록시 모두 실패: {url}. "
                "GitHub Actions IP 차단일 수 있습니다. "
                "JINA_API_KEY Secret 등록을 시도해 보세요."
            ) from jina_exc


def _extract_event_id(href: str | None) -> str | None:
    if not href:
        return None
    match = EVENT_ID_PATTERN.search(href)
    return match.group(1) if match else None


def _is_sunday_maple_title(title: str) -> bool:
    return SUNDAY_MAPLE_KEYWORD in title


def find_latest_sunday_maple_html(soup: BeautifulSoup) -> SundayMapleEvent | None:
    banner = soup.select_one("ul.event_all_banner")
    if banner is None:
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
        logger.info("썬데이 이벤트 발견(HTML): id=%s, title=%s", event_id, title)
        return SundayMapleEvent(
            event_id=event_id,
            title=title,
            period=period,
            detail_url=detail_url,
        )

    return None


def find_latest_sunday_maple_jina(text: str) -> SundayMapleEvent | None:
    match = JINA_EVENT_LINK_PATTERN.search(text)
    if match is None:
        return None

    title = match.group(1)
    event_id = match.group(2)
    period = ""
    period_match = JINA_PERIOD_PATTERN.search(text, match.start(), match.start() + 400)
    if period_match:
        period = period_match.group(1).strip()

    detail_url = EVENT_DETAIL_URL.format(event_id=event_id)
    logger.info("썬데이 이벤트 발견(Jina): id=%s, title=%s", event_id, title)
    return SundayMapleEvent(
        event_id=event_id,
        title=title,
        period=period,
        detail_url=detail_url,
    )


def find_latest_sunday_maple(soup: BeautifulSoup) -> SundayMapleEvent | None:
    event = find_latest_sunday_maple_html(soup)
    if event is None:
        logger.error("이벤트 목록(.event_all_banner)을 찾을 수 없습니다.")
    return event


def extract_detail_metadata_html(soup: BeautifulSoup) -> tuple[str, str]:
    title = ""
    title_node = soup.select_one("p.qs_title span")
    if title_node:
        title = title_node.get_text(strip=True)

    period = ""
    period_node = soup.select_one("span.event_date")
    if period_node:
        period = period_node.get_text(strip=True)

    return title or "", period or ""


def extract_detail_metadata_jina(text: str) -> tuple[str, str]:
    title = SUNDAY_MAPLE_KEYWORD
    if "스페셜" in text:
        title = "스페셜 썬데이 메이플"

    period = ""
    period_match = re.search(r"(\d{4}년\s*\d{2}월\s*\d{2}일[^<\n]*)", text)
    if period_match:
        period = period_match.group(1).strip()

    return title, period


def extract_main_image_url_html(soup: BeautifulSoup) -> str | None:
    content = soup.select_one(".new_board_con")
    if content is None:
        return None

    for img in content.find_all("img"):
        src = img.get("src") or img.get("data-src")
        if not src:
            continue
        absolute = urljoin(BASE_URL, src)
        if MAIN_IMAGE_HOST in absolute and "_board/" in absolute:
            return absolute

    return None


def extract_main_image_url_jina(text: str) -> str | None:
    match = BOARD_IMAGE_PATTERN.search(text)
    return match.group(0) if match else None


def extract_main_image_url(soup: BeautifulSoup) -> str | None:
    url = extract_main_image_url_html(soup)
    if url is None:
        logger.error("본문 이미지(lwi.nexon.com) URL을 찾을 수 없습니다.")
    return url


def download_image(session: requests.Session, url: str) -> bytes:
    headers = {
        "User-Agent": USER_AGENT,
        "Referer": BASE_URL,
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    }
    response = _request_with_retry(
        session,
        url,
        timeout=(20, 60),
        headers=headers,
    )
    if not response.content:
        raise ScraperError(f"이미지가 비어 있습니다: {url}")
    logger.info("이미지 다운로드 완료 (%d bytes)", len(response.content))
    return response.content


def create_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(BROWSER_HEADERS)
    return session


def fetch_sunday_maple_event(
    session: requests.Session | None = None,
) -> SundayMapleEvent | None:
    session = session or create_session()

    list_content, list_source = fetch_page(session, EVENT_LIST_URL)
    if list_source == "html":
        event = find_latest_sunday_maple(BeautifulSoup(list_content, "lxml"))
    else:
        event = find_latest_sunday_maple_jina(list_content)
        if event is None:
            logger.error("Jina 응답에서 썬데이 메이플 이벤트를 찾을 수 없습니다.")

    if event is None:
        return None

    detail_content, detail_source = fetch_page(session, event.detail_url)
    if detail_source == "html":
        detail_soup = BeautifulSoup(detail_content, "lxml")
        detail_title, detail_period = extract_detail_metadata_html(detail_soup)
        image_url = extract_main_image_url_html(detail_soup)
    else:
        detail_title, detail_period = extract_detail_metadata_jina(detail_content)
        image_url = extract_main_image_url_jina(detail_content)
        if image_url is None:
            logger.error("Jina 응답에서 본문 이미지 URL을 찾을 수 없습니다.")

    return SundayMapleEvent(
        event_id=event.event_id,
        title=detail_title or event.title,
        period=detail_period or event.period,
        detail_url=event.detail_url,
        image_url=image_url,
    )
