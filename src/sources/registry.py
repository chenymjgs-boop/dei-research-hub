"""Curated DEI source list (v2).

Strategy
--------
1) **Direct publisher RSS** wherever the publisher exposes a working feed —
   gives us real article URLs (not Google News redirect links), no extra
   network round-trips, and zero risk of Google News rate-limiting.
2) **HTML list scrape** for sources that have a public article-list page but
   no RSS (rare; most reputable publishers have RSS).
3) **Google News RSS fallback** only for high-value publishers that don't
   expose a usable direct feed (HBR, BCG, SHRM, WEF, etc.). Items will have
   `news.google.com/rss/articles/...` URLs — that's the unavoidable trade-off.

v2 source categories (must match Bitable single-select options):
  academic / consulting / international_org / media / regulator / china_local / wechat

Competitor flag (v2):
  Sources marked is_competitor=True flow into the regular pillar streams BUT
  also get an extra `competitor_intelligence` field from the analyzer and a
  dedicated section in the weekly report.

Last verified: see commit history. If a feed starts returning 0 entries
or HTTP 4xx, run `python -m src.delivery.verify` and `python main.py preview`.
"""
from __future__ import annotations

from typing import List
from urllib.parse import quote_plus

from .base import HTMLListSource, RSSSource, Source


def gnews(query: str, lang: str = "en") -> str:
    """Build a Google News RSS URL for a search query."""
    if lang == "zh":
        params = "hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
    else:
        params = "hl=en-US&gl=US&ceid=US:en"
    return f"https://news.google.com/rss/search?q={quote_plus(query)}&{params}"


# ============================================================================
# Academic & Research
# ============================================================================
ACADEMIC: List[Source] = [
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
    # HBR has no usable direct feed — fall back to Google News
    RSSSource(
        name="HBR (via Google News)",
        feed_url=gnews("(diversity OR inclusion OR equity OR belonging) site:hbr.org"),
        category="academic",
    ),
    # v2.1 NEW: Harvard Law School Forum on Corporate Governance — direct RSS
    # General corp gov feed; keep DEI filter to focus on DEI-related governance items
    RSSSource(
        name="Harvard Law Forum on Corporate Governance",
        feed_url="https://corpgov.law.harvard.edu/feed/",
        category="academic",
    ),
]

# ============================================================================
# Consulting & DEI Specialist Firms (incl. competitors)
# ============================================================================
CONSULTING: List[Source] = [
    # Direct RSS — McKinsey insights
    RSSSource(
        name="McKinsey Insights",
        feed_url="https://www.mckinsey.com/insights/rss",
        category="consulting",
    ),
    # === v2 Tier 1 NEW: direct-RSS competitors & DEI specialists ===
    RSSSource(
        name="Seramount",
        feed_url="https://seramount.com/feed/",
        category="consulting",
        is_competitor=True,  # Working Mother Media 旗下，DEI 评测+咨询竞品
    ),
    RSSSource(
        name="Paradigm IQ",
        feed_url="https://www.paradigmiq.com/feed/",
        category="consulting",
        is_competitor=True,  # 评测 + 包容性领导力培训竞品
    ),
    RSSSource(
        name="The Diversity Movement",
        feed_url="https://thediversitymovement.com/feed/",
        category="consulting",
        is_competitor=True,  # 美国 DEI 咨询+内容机构
    ),
    RSSSource(
        name="Global Diversity Practice",
        feed_url="https://globaldiversitypractice.com/feed/",
        category="consulting",
        is_competitor=True,  # 英国老牌 DEI 培训+认证
    ),
    RSSSource(
        name="Champions of Change Coalition",
        feed_url="https://championsofchangecoalition.org/feed/",
        category="consulting",
        is_competitor=False,  # 非竞品：男性盟友联盟，方法论可借鉴
        bypass_dei_filter=True,  # DEI 专项 feed，跳过关键词过滤
    ),
    # === v2 Tier 1: Google News fallback (no direct feed) ===
    RSSSource(
        name="Catalyst (via Google News)",
        feed_url=gnews("site:catalyst.org"),
        category="consulting",
        is_competitor=True,  # 全球最大 DEI 研究+咨询机构，竞品
    ),
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
        name="Mercer DEI (via Google News)",
        feed_url=gnews("(diversity OR inclusion OR DEI) site:mercer.com"),
        category="consulting",
        is_competitor=True,
    ),
    RSSSource(
        name="Korn Ferry DEI (via Google News)",
        # Restrict to insights/research subpaths and exclude staff bio URLs
        feed_url=gnews(
            "(diversity OR inclusion OR DEI OR equity OR belonging) "
            "(site:kornferry.com/insights OR site:kornferry.com/research)"
        ),
        category="consulting",
        is_competitor=True,
    ),
]

# ============================================================================
# International Organizations
# ============================================================================
INTERNATIONAL: List[Source] = [
    RSSSource(
        name="UN News (en)",
        feed_url="https://news.un.org/feed/subscribe/en/news/all/rss.xml",
        category="international_org",
    ),
    RSSSource(
        name="ILOSTAT Blog",
        feed_url="https://ilostat.ilo.org/feed/",
        category="international_org",
    ),
    RSSSource(
        name="UN Women (via Google News)",
        feed_url=gnews("site:unwomen.org"),
        category="international_org",
    ),
    RSSSource(
        name="World Economic Forum DEI (via Google News)",
        feed_url=gnews(
            "(diversity OR equity OR inclusion OR belonging) site:weforum.org"
        ),
        category="international_org",
    ),
    RSSSource(
        name="World Bank Gender (via Google News)",
        feed_url=gnews('"gender equality" OR diversity site:worldbank.org'),
        category="international_org",
    ),
    RSSSource(
        name="OECD Equality (via Google News)",
        feed_url=gnews(
            '(diversity OR "gender equality" OR inclusion) site:oecd.org'
        ),
        category="international_org",
    ),
]

# ============================================================================
# Industry Media
# ============================================================================
MEDIA: List[Source] = [
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
    RSSSource(
        name="SHRM DEI (via Google News)",
        feed_url=gnews("(diversity OR inclusion OR equity) site:shrm.org"),
        category="media",
    ),
    # === v2 Tier 1 NEW ===
    RSSSource(
        name="Lean In (via Google News)",
        feed_url=gnews("site:leanin.org"),
        category="media",
    ),
    RSSSource(
        name="DiversityInc (via Google News)",
        feed_url=gnews("site:diversityinc.com"),
        category="media",
    ),
    # v2.1 NEW: The Advocate — direct RSS, LGBTQ+ + DEI 反弹报道
    RSSSource(
        name="The Advocate",
        feed_url="https://www.advocate.com/rss.xml",
        category="media",
    ),
    # v2.1 NEW: Bloomberg DEI 通过 GN 兜底（直链有 paywall）
    RSSSource(
        name="Bloomberg DEI / Equality (via Google News)",
        feed_url=gnews("(diversity OR inclusion OR DEI OR equity) site:bloomberg.com"),
        category="media",
    ),
    # v2.1 NEW: Littler 美国劳动法权威，DEI 案件分析
    RSSSource(
        name="Littler DEI (via Google News)",
        feed_url=gnews("(diversity OR inclusion OR DEI OR EEOC) site:littler.com"),
        category="media",
    ),
]

# ============================================================================
# Regulators (v2 NEW — direct scraping of regulatory bodies)
# Phase 3b: native scrapers replace GN fallback for higher accuracy.
# ============================================================================
REGULATOR: List[Source] = [
    # === Direct RSS ===
    RSSSource(
        name="SEC Press Releases",
        feed_url="https://www.sec.gov/news/pressreleases.rss",
        category="regulator",
        # SEC pubs ~25/wk; mostly enforcement/SPAC, ~10% touch DEI/human capital.
        # Keep DEI filter ON — only items mentioning ESG/DEI/human capital pass.
    ),
    # === Native HTML scrapers ===
    HTMLListSource(
        name="US EEOC Newsroom",
        list_url="https://www.eeoc.gov/newsroom",
        link_selector="article h2 a",
        title_selector=None,  # text content of <a> is the title
        category="regulator",
        bypass_dei_filter=True,  # EEOC newsroom = 100% workplace anti-discrimination
    ),
    HTMLListSource(
        name="EFRAG (ESRS / CSRD)",
        list_url="https://www.efrag.org/news",
        link_selector="article h3 a",
        title_selector=None,
        category="regulator",
        # NO bypass — EFRAG publishes mostly governance/accounting items (TEG
        # composition, IASB consultations, GHG protocol, conferences). Keep the
        # DEI filter so only items mentioning ESRS / CSRD / social topics pass.
    ),
    HTMLListSource(
        name="EU Commission · Sustainable Finance",
        list_url="https://finance.ec.europa.eu/news_en",
        link_selector=".ecl-content-block a[href*='/news/']",
        title_selector=None,
        category="regulator",
        # NO bypass — content is mixed (sanctions, banking, etc), keep DEI filter
    ),
    # === GN fallback for SPA-rendered HKEX (no clean DOM to scrape) ===
    RSSSource(
        name="HKEX ESG (via Google News)",
        feed_url=gnews('"ESG" OR "diversity" site:hkex.com.hk'),
        category="regulator",
    ),
]

# ============================================================================
# China-focused (Chinese-language)
# ============================================================================
CHINA: List[Source] = [
    # Direct RSS — 36Kr's general feed (heavy DEI filter required downstream)
    RSSSource(
        name="36氪",
        feed_url="https://36kr.com/feed",
        category="china_local",
        region="china",
    ),
    # Google News fallback — China-specific DEI queries
    RSSSource(
        name="中国职场多元 · 综合（Google News 中文）",
        feed_url=gnews("多元 包容 职场 OR 性别平等 企业", lang="zh"),
        category="china_local",
        region="china",
    ),
    RSSSource(
        name="中国 ESG 与员工多元（Google News 中文）",
        feed_url=gnews("ESG 多元 OR 性别 OR 包容 中国 企业", lang="zh"),
        category="china_local",
        region="china",
    ),
    RSSSource(
        name="中国残障与无障碍（Google News 中文）",
        feed_url=gnews("残障 就业 OR 无障碍 企业 中国", lang="zh"),
        category="china_local",
        region="china",
    ),
    RSSSource(
        name="中国育儿与照护职场（Google News 中文）",
        feed_url=gnews("生育 OR 育儿 OR 照护 职场 OR 企业 中国", lang="zh"),
        category="china_local",
        region="china",
    ),
    # v2 NEW: focused on China outbound + ESG disclosure
    RSSSource(
        name="中国出海企业 ESG / 用工合规（Google News 中文）",
        feed_url=gnews("中国企业 出海 ESG OR 用工 OR 多元", lang="zh"),
        category="china_local",
        region="china",
    ),
    RSSSource(
        name="港交所 ESG 披露（Google News 中文）",
        feed_url=gnews("港交所 ESG 披露 OR 上市 多元", lang="zh"),
        category="china_local",
        region="china",
    ),
    # v2.1 NEW: 上海市政府 / 商务委 ESG 政策
    RSSSource(
        name="上海市 ESG / 涉外企业政策（Google News 中文）",
        feed_url=gnews(
            "上海 ESG OR 涉外企业 OR 涉外多元 (site:sh.gov.cn OR site:sww.sh.gov.cn)",
            lang="zh",
        ),
        category="china_local",
        region="china",
    ),
]

# ============================================================================
# WeChat (Phase 3c)
# Layer 1 (Wechat2RSS public) currently has NO DEI accounts in coverage — gap.
# Layer 2 (self-hosted WeWe RSS) requires VPS — Phase 3d.
# Layer 3 (manual intake): activated only if FEISHU_MANUAL_INTAKE_CHAT_ID is set.
# ============================================================================
from .wechat_manual import WechatManualIntakeSource  # noqa: E402

WECHAT: List[Source] = [
    WechatManualIntakeSource(),
]

ALL_SOURCES: List[Source] = (
    ACADEMIC + CONSULTING + INTERNATIONAL + MEDIA + REGULATOR + CHINA + WECHAT
)
