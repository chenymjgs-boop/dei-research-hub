"""Source base classes: RawItem dataclass + RSS / HTML scraper helpers."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterable, List, Optional

import feedparser
import requests
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential

from ..utils import clean_text, get_logger, now_utc, parse_date, url_hash

LOGGER = get_logger(__name__)
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36 DEIResearchAssistant/1.0"
)
HEADERS = {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8"}


@dataclass
class RawItem:
    """A single piece of content fetched from a source, before LLM processing."""

    title: str
    url: str
    source_name: str
    source_category: str  # academic / consulting / international / media / china
    published_at: Optional[datetime] = None
    summary: str = ""  # raw teaser/abstract from source feed
    region: str = "global"  # "global" | "china"
    authors: List[str] = field(default_factory=list)

    @property
    def hash(self) -> str:
        return url_hash(self.url)

    def is_recent(self, lookback_days: int) -> bool:
        if not self.published_at:
            return True  # if no date, keep — many sources don't publish dates reliably
        cutoff = now_utc() - timedelta(days=lookback_days)
        return self.published_at >= cutoff


class Source(ABC):
    name: str
    category: str
    region: str = "global"

    @abstractmethod
    def fetch(self, lookback_days: int, limit: int) -> Iterable[RawItem]:
        ...


# ---------- Generic implementations ----------


@retry(stop=stop_after_attempt(2), wait=wait_exponential(min=2, max=8), reraise=True)
def http_get(url: str, timeout: int = 20) -> requests.Response:
    resp = requests.get(url, headers=HEADERS, timeout=timeout)
    resp.raise_for_status()
    return resp


class RSSSource(Source):
    """Generic RSS / Atom feed source."""

    feed_url: str

    def __init__(
        self,
        name: str,
        feed_url: str,
        category: str,
        region: str = "global",
    ) -> None:
        self.name = name
        self.feed_url = feed_url
        self.category = category
        self.region = region

    def fetch(self, lookback_days: int, limit: int) -> Iterable[RawItem]:
        LOGGER.info("Fetching RSS: %s", self.name)
        try:
            # Fetch via requests (with our UA) then hand bytes to feedparser.
            # feedparser's built-in fetcher is blocked by some hosts (Google News).
            resp = http_get(self.feed_url, timeout=20)
            feed = feedparser.parse(resp.content)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("RSS fetch failed for %s: %s", self.name, exc)
            return
        if not feed.entries:
            LOGGER.info("  → 0 entries from %s", self.name)
            return

        count = 0
        for entry in feed.entries:
            if count >= limit:
                break
            url = entry.get("link") or ""
            title = clean_text(entry.get("title", ""), max_chars=300)
            if not url or not title:
                continue
            summary = clean_text(
                entry.get("summary") or entry.get("description") or "",
                max_chars=2000,
            )
            published = parse_date(
                entry.get("published") or entry.get("updated") or entry.get("created")
            )
            authors: List[str] = []
            if "authors" in entry:
                authors = [a.get("name", "") for a in entry.authors if a.get("name")]
            elif entry.get("author"):
                authors = [entry["author"]]

            item = RawItem(
                title=title,
                url=url,
                source_name=self.name,
                source_category=self.category,
                published_at=published,
                summary=summary,
                region=self.region,
                authors=authors,
            )
            if not item.is_recent(lookback_days):
                continue
            yield item
            count += 1


class HTMLListSource(Source):
    """Generic source: list page → CSS selector → article links."""

    def __init__(
        self,
        name: str,
        list_url: str,
        link_selector: str,
        title_selector: Optional[str],
        category: str,
        region: str = "global",
        base_url: Optional[str] = None,
    ) -> None:
        self.name = name
        self.list_url = list_url
        self.link_selector = link_selector
        self.title_selector = title_selector
        self.category = category
        self.region = region
        self.base_url = base_url or self._origin(list_url)

    @staticmethod
    def _origin(url: str) -> str:
        from urllib.parse import urlparse

        p = urlparse(url)
        return f"{p.scheme}://{p.netloc}"

    def fetch(self, lookback_days: int, limit: int) -> Iterable[RawItem]:
        LOGGER.info("Fetching HTML list: %s", self.name)
        try:
            resp = http_get(self.list_url)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("HTML fetch failed for %s: %s", self.name, exc)
            return

        soup = BeautifulSoup(resp.text, "lxml")
        seen: set[str] = set()
        count = 0
        for link_el in soup.select(self.link_selector):
            if count >= limit:
                break
            href = link_el.get("href", "")
            if not href:
                continue
            if href.startswith("/"):
                href = self.base_url.rstrip("/") + href
            elif not href.startswith("http"):
                continue
            if href in seen:
                continue
            seen.add(href)

            title_el = (
                link_el.select_one(self.title_selector)
                if self.title_selector
                else link_el
            )
            title = clean_text(title_el.get_text(strip=True) if title_el else "", 300)
            if not title or len(title) < 10:
                continue

            yield RawItem(
                title=title,
                url=href,
                source_name=self.name,
                source_category=self.category,
                region=self.region,
                summary="",
                published_at=None,
            )
            count += 1
