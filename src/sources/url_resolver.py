"""Resolve Google News redirect URLs to their final article URLs.

Google News (post-2024) wraps every RSS link in an opaque tracker URL
(news.google.com/rss/articles/CBMi...). The redirect happens via an
authenticated batchexecute call, not HTTP 30x — so we can't just follow
redirects. We use the `googlenewsdecoder` package which performs the
two-step handshake (fetch signature → POST to batchexecute → extract
final URL).

If resolution fails for any reason, we fall back to the original URL —
better a click-through tracker link in Feishu than dropping the item.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Iterable

from googlenewsdecoder import gnewsdecoder

from ..utils import get_logger

LOGGER = get_logger(__name__)

_GN_PREFIX = "https://news.google.com/"


def is_google_news_url(url: str) -> bool:
    return url.startswith(_GN_PREFIX) and "/articles/" in url


def _resolve_one(url: str) -> str:
    try:
        result = gnewsdecoder(url, interval=1)
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("Google News resolver crashed for %s: %s", url[:80], exc)
        return url
    if isinstance(result, dict) and result.get("status") and result.get("decoded_url"):
        return result["decoded_url"]
    LOGGER.warning("Google News resolver returned no URL for %s", url[:80])
    return url


def resolve_google_news_urls(urls: Iterable[str], max_workers: int = 4) -> Dict[str, str]:
    """Resolve Google News URLs in parallel. Returns map of original→final URL.

    Only Google-News-hosted URLs are touched; everything else is skipped.
    """
    targets = [u for u in dict.fromkeys(urls) if is_google_news_url(u)]
    if not targets:
        return {}

    LOGGER.info("Resolving %d Google News URLs (workers=%d)", len(targets), max_workers)
    out: Dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_resolve_one, u): u for u in targets}
        for fut in as_completed(futures):
            orig = futures[fut]
            out[orig] = fut.result()

    n_changed = sum(1 for k, v in out.items() if k != v)
    LOGGER.info(
        "Google News resolution: %d/%d resolved (%d fell back to tracker URL)",
        n_changed, len(targets), len(targets) - n_changed,
    )
    return out
