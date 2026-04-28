"""Curated DEI source list.

Strategy
--------
1) **Direct publisher RSS** wherever the publisher exposes a working feed —
   gives us real article URLs (not Google News redirect links), no extra
   network round-trips, and zero risk of Google News rate-limiting.
2) **Google News RSS fallback** only for high-value publishers that don't
   expose a usable direct feed (HBR, BCG, Catalyst, SHRM, WEF, etc.).
   These items will have `news.google.com/rss/articles/...` URLs — that's
   the unavoidable trade-off until / unless we add a URL-decoding step.

Categories: academic / consulting / international / media / china

Last verified: see commit history. If a feed starts returning 0 entries
or HTTP 4xx, check `python -m src.sources.registry --probe`.
"""
from __future__ import annotations

from typing import List
from urllib.parse import quote_plus

from .base import RSSSource, Source


def gnews(query: str, lang: str = "en") -> str:
    """Build a Google News RSS URL for a search query."""
    if lang == "zh":
        params = "hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
    else:
        params = "hl=en-US&gl=US&ceid=US:en"
    return f"https://news.google.com/rss/search?q={quote_plus(query)}&{params}"


# ---- Academic & Research ----
ACADEMIC: List[Source] = [
    # Direct RSS — real article URLs
    RSSSource(
        name="MIT Sloan Management Review",
        feed_url="https://sloanreview.mit.edu/feed/",
        category="academic",
    ),
    RSSSource(
        name="Stanford Social Innovation Review",
        feed_url="https://ssir.org/site/rss_2.0",
        category="academic",
    ),
    RSSSource(
        name="Academy of Management Journal",
        feed_url="https://journals.aom.org/action/showFeed?type=etoc&feed=rss&jc=amj",
        category="academic",
    ),
    # Google News fallback — HBR has no working direct feed
    RSSSource(
        name="HBR (via Google News)",
        feed_url=gnews("(diversity OR inclusion OR equity OR belonging) site:hbr.org"),
        category="academic",
    ),
]

# ---- Consulting & Research Firms ----
CONSULTING: List[Source] = [
    # Direct RSS
    RSSSource(
        name="McKinsey Insights",
        feed_url="https://www.mckinsey.com/insights/rss",
        category="consulting",
    ),
    # Google News fallback — these firms don't expose usable direct feeds
    RSSSource(
        name="BCG DEI (via Google News)",
        feed_url=gnews("(diversity OR inclusion OR equity) site:bcg.com"),
        category="consulting",
    ),
    RSSSource(
        name="Deloitte DEI (via Google News)",
        feed_url=gnews("(diversity OR inclusion OR equity) site:deloitte.com"),
        category="consulting",
    ),
    RSSSource(
        name="Catalyst (via Google News)",
        feed_url=gnews("site:catalyst.org"),
        category="consulting",
    ),
]

# ---- International Organizations ----
INTERNATIONAL: List[Source] = [
    # Direct RSS
    RSSSource(
        name="UN News (en)",
        feed_url="https://news.un.org/feed/subscribe/en/news/all/rss.xml",
        category="international",
    ),
    RSSSource(
        name="ILOSTAT Blog",
        feed_url="https://ilostat.ilo.org/feed/",
        category="international",
    ),
    # Google News fallback
    RSSSource(
        name="UN Women (via Google News)",
        feed_url=gnews("site:unwomen.org"),
        category="international",
    ),
    RSSSource(
        name="World Economic Forum DEI (via Google News)",
        feed_url=gnews(
            "(diversity OR equity OR inclusion OR belonging) site:weforum.org"
        ),
        category="international",
    ),
    RSSSource(
        name="World Bank Gender (via Google News)",
        feed_url=gnews('"gender equality" OR diversity site:worldbank.org'),
        category="international",
    ),
    RSSSource(
        name="OECD Equality (via Google News)",
        feed_url=gnews(
            '(diversity OR "gender equality" OR inclusion) site:oecd.org'
        ),
        category="international",
    ),
]

# ---- Industry Media ----
MEDIA: List[Source] = [
    # Direct RSS
    RSSSource(
        name="HR Dive",
        feed_url="https://www.hrdive.com/feeds/news/",
        category="media",
    ),
    RSSSource(
        name="Fast Company Workplace Evolution",
        feed_url="https://www.fastcompany.com/section/workplace-evolution/rss",
        category="media",
    ),
    RSSSource(
        name="HR Executive",
        feed_url="https://hrexecutive.com/feed/",
        category="media",
    ),
    RSSSource(
        name="TalentCulture",
        feed_url="https://talentculture.com/feed/",
        category="media",
    ),
    RSSSource(
        name="Forbes News",
        feed_url="https://www.forbes.com/news/feed2/",
        category="media",
    ),
    RSSSource(
        name="Quartz",
        feed_url="https://qz.com/feed",
        category="media",
    ),
    # Google News fallback — SHRM has no usable direct feed
    RSSSource(
        name="SHRM DEI (via Google News)",
        feed_url=gnews("(diversity OR inclusion OR equity) site:shrm.org"),
        category="media",
    ),
]

# ---- China-focused (Chinese-language) ----
CHINA: List[Source] = [
    # Direct RSS — 36Kr's general feed (heavy DEI filter required downstream)
    RSSSource(
        name="36氪",
        feed_url="https://36kr.com/feed",
        category="china",
        region="china",
    ),
    # Google News fallback — China-specific DEI queries
    RSSSource(
        name="中国职场多元 · 综合（Google News 中文）",
        feed_url=gnews("多元 包容 职场 OR 性别平等 企业", lang="zh"),
        category="china",
        region="china",
    ),
    RSSSource(
        name="中国 ESG 与员工多元（Google News 中文）",
        feed_url=gnews("ESG 多元 OR 性别 OR 包容 中国 企业", lang="zh"),
        category="china",
        region="china",
    ),
    RSSSource(
        name="中国残障与无障碍（Google News 中文）",
        feed_url=gnews("残障 就业 OR 无障碍 企业 中国", lang="zh"),
        category="china",
        region="china",
    ),
    RSSSource(
        name="中国育儿与照护职场（Google News 中文）",
        feed_url=gnews("生育 OR 育儿 OR 照护 职场 OR 企业 中国", lang="zh"),
        category="china",
        region="china",
    ),
]

ALL_SOURCES: List[Source] = ACADEMIC + CONSULTING + INTERNATIONAL + MEDIA + CHINA
