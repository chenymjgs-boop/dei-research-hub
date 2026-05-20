"""WeChat Layer 3 — manual intake via Feishu group chat.

Background (see brief §3.7):
    Wechat2RSS public service does NOT cover the 9 user-listed MNC recruiting
    accounts. Self-hosted WeWe RSS (Layer 2) requires a VPS + WeChat reading-app
    sub-account — deferred to Phase 3d.

This module implements Layer 3 (the cheapest, lowest-tech option):
    Anyone in the company sees an interesting WeChat article in their phone,
    long-presses the link to copy it, and pastes it into a designated Feishu
    group. This module polls that group every fetch cycle, extracts every
    `mp.weixin.qq.com` URL, and turns each into a RawItem (fetching the
    article HTML on the fly).

Permissions required on the Feishu app:
    im:message               (already granted; needed to read messages)
    im:chat:readonly         (already granted; needed to identify chat)

Activation:
    Set FEISHU_MANUAL_INTAKE_CHAT_ID in .env to the chat_id of the group used
    for link forwarding. If unset, this source produces no items (silent skip).

Future improvements (NOT in this scaffold):
    - Webhook event subscription instead of polling (lower latency, fewer API calls)
    - Acknowledge-then-archive: post a 👍 reaction after the item is ingested
    - De-dup against DB before fetching (currently relies on hash dedup downstream)
"""
from __future__ import annotations

import re
import time
from typing import Iterable, List

import requests

from ..config import SETTINGS
from ..utils import clean_text, get_logger
from .base import HEADERS, RawItem, Source, http_get

LOGGER = get_logger(__name__)

FEISHU_BASE = "https://open.feishu.cn/open-apis"
WECHAT_URL_PATTERN = re.compile(r'https?://mp\.weixin\.qq\.com/[^\s<>"\')]+', re.IGNORECASE)


class WechatManualIntakeSource(Source):
    """Polls a Feishu group for pasted WeChat article URLs and fetches each."""

    name = "WeChat Manual Intake"
    category = "wechat"
    region = "china"
    is_competitor = False
    bypass_dei_filter = True       # human curator already vetted before pasting
    source_subtype = "wechat_manual"

    def __init__(self, chat_id: str | None = None) -> None:
        self.chat_id = chat_id or SETTINGS.feishu_manual_intake_chat_id

    def fetch(self, lookback_days: int, limit: int) -> Iterable[RawItem]:
        if not self.chat_id or "xxxx" in self.chat_id.lower():
            LOGGER.info("FEISHU_MANUAL_INTAKE_CHAT_ID not set — skipping manual intake")
            return
        token = _feishu_tenant_token()
        if not token:
            LOGGER.warning("Feishu auth failed — skipping manual intake")
            return

        cutoff_ms = int((time.time() - lookback_days * 86400) * 1000)
        urls = _list_recent_wechat_urls(token, self.chat_id, cutoff_ms, limit * 4)
        LOGGER.info("Manual intake: found %d WeChat URLs in chat %s", len(urls), self.chat_id)

        seen: set[str] = set()
        count = 0
        for url, msg_ts in urls:
            if count >= limit:
                break
            if url in seen:
                continue
            seen.add(url)
            item = _fetch_wechat_article(url, msg_ts)
            if item:
                yield item
                count += 1


def _feishu_tenant_token() -> str | None:
    if not SETTINGS.feishu_app_id or "xxxx" in SETTINGS.feishu_app_id.lower():
        return None
    try:
        r = requests.post(
            f"{FEISHU_BASE}/auth/v3/tenant_access_token/internal",
            json={
                "app_id": SETTINGS.feishu_app_id,
                "app_secret": SETTINGS.feishu_app_secret,
            },
            timeout=10,
        )
        data = r.json()
        if data.get("code") == 0:
            return data["tenant_access_token"]
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("Feishu auth failed: %s", exc)
    return None


def _list_recent_wechat_urls(token: str, chat_id: str, cutoff_ms: int, max_msgs: int) -> List[tuple[str, int]]:
    """Pull recent messages from the chat and extract every mp.weixin.qq.com URL.

    Returns list of (url, message_create_time_ms) tuples, newest first.
    """
    out: List[tuple[str, int]] = []
    page_token = ""
    pulled = 0
    while pulled < max_msgs:
        params = {
            "container_id_type": "chat",
            "container_id": chat_id,
            "page_size": 50,
            "sort_type": "ByCreateTimeDesc",
        }
        if page_token:
            params["page_token"] = page_token
        try:
            r = requests.get(
                f"{FEISHU_BASE}/im/v1/messages",
                headers={"Authorization": f"Bearer {token}"},
                params=params,
                timeout=15,
            )
            data = r.json()
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("List messages failed: %s", exc)
            break
        if data.get("code") != 0:
            LOGGER.warning("List messages API err: %s", data.get("msg", "")[:120])
            break

        items = (data.get("data") or {}).get("items") or []
        if not items:
            break
        for m in items:
            create_time = int(m.get("create_time", "0"))
            if create_time < cutoff_ms:
                # Past lookback window — stop (sorted desc)
                return out
            # The message body is JSON-serialized in 'body.content'
            body = (m.get("body") or {}).get("content") or ""
            for match in WECHAT_URL_PATTERN.findall(body):
                out.append((match, create_time))
            pulled += 1
        page_token = (data.get("data") or {}).get("page_token", "")
        if not page_token or not (data.get("data") or {}).get("has_more"):
            break
    return out


def _fetch_wechat_article(url: str, ts_ms: int) -> RawItem | None:
    """Fetch a public mp.weixin.qq.com article and parse title + brief body."""
    from datetime import datetime, timezone

    try:
        resp = http_get(url, timeout=15)
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("Fetch wechat article failed for %s: %s", url, exc)
        return None

    from bs4 import BeautifulSoup

    soup = BeautifulSoup(resp.text, "lxml")
    # WeChat articles: title in <h1 id="activity-name"> or <h2 class="rich_media_title">
    title_el = (
        soup.select_one("#activity-name")
        or soup.select_one(".rich_media_title")
        or soup.select_one("title")
    )
    title = clean_text(title_el.get_text(strip=True) if title_el else "", 300)
    if not title:
        return None

    # Author / publisher account name
    author_el = soup.select_one("#js_name") or soup.select_one(".profile_nickname")
    author = clean_text(author_el.get_text(strip=True) if author_el else "", 100)

    # Body — first ~3000 chars of article content
    body_el = soup.select_one("#js_content") or soup.select_one(".rich_media_content")
    body_text = clean_text(body_el.get_text(separator=" ", strip=True) if body_el else "", 4000)

    return RawItem(
        title=title,
        url=url,
        source_name=f"微信·{author}" if author else "WeChat (manual intake)",
        source_category="wechat",
        published_at=datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc),
        summary=body_text,
        region="china",
        authors=[author] if author else [],
        is_competitor=False,
        source_subtype="wechat_manual",
    )
