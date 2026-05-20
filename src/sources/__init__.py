"""Source registry — every source returns a list of `RawItem`."""
from __future__ import annotations

import re
from typing import List

from .base import RawItem, Source
from .registry import ALL_SOURCES

# Cheap local pre-filter: only items whose title/summary mention at least one of
# these keywords are sent to the LLM. Prevents wasting tokens on non-DEI content
# from broad feeds (e.g. MIT Sloan's general feed).
# NOTE on word boundaries:
# Abbreviations (DEI, DEIB, LGBT, LGBTQ) need \b on BOTH sides — without them
# they match inside ordinary words ("editing", "media", etc.). The previous
# version included "EDI" without boundaries, causing massive false positives;
# we drop "EDI" entirely because it overwhelmingly means "Electronic Data
# Interchange" in business contexts. "race" needs \b on both sides too,
# otherwise it matches inside "embrace", "trace", etc.
# Chinese keywords don't need \b (Chinese has no word boundaries in this sense).
_DEI_KEYWORDS = re.compile(
    r"""(
        \b(?: DEI | DEIB | LGBTQ | LGBT | ERG | CSRD | ESRS )\b
        | diversity | inclusion | inclusive | equity | equality | belonging
        | gender | women | woman | intersection
        | racial | racism | \brace\b | ethnic | minority | minorities
        | disability | disabilities | accessib | neurodiver
        | bias | discrimination | harassment | parental | caregiv
        | multicultural | inter-cultural | cross-cultural
        | human\ capital | pay\ gap | anti-trans
        | 多元 | 包容 | 平等 | 公平 | 性别 | 女性 | 残障 | 残疾 | 无障碍
        | 性少数 | 育儿 | 生育 | 照护 | 跨文化 | 反歧视 | 歧视
        | 神经多样 | 多元化 | 共融 | 反性骚扰 | 员工多元
        | \bESG\b | 出海 | 港交所 | 披露 | 上市 \s* (?: 公司 | 企业 )
    )""",
    re.IGNORECASE | re.VERBOSE,
)

# Exclusion patterns: false positives we want to drop even if a DEI keyword
# was matched. Tuned for noise we have actually observed in the wild.
_NOISE_PATTERNS = re.compile(
    r"""(
        \bprivate\s+equity\b           # finance, not DEI
      | \bequity\s+(market|fund|firm|investment|valuation|partner)
      | \bdebt[-\s]?equity\b           # "hybrid debt-equity strategy" — finance, not DEI
      | \b(commodity|brand)\s+equity\b
      | \bjob\s+posting\b | \bcareers?\b\s+@ | \bnow\s+hiring\b
      | \bIT\s+Consultant\b
      | \bgaza\b | \blebanon\b | \bukraine\b | \brussia\b | \bsudan\b
      | \bwar\s+casualt | \bdisplaced\s+(women|people)\b
      | \bproject\s+P\d{4,}                # World Bank project IDs
      | \bcrypto | \btoken\s+sale
      # Chinese-specific finance/policy/commerce false positives
      | 多元主体 | 货币多元 | 多元化\s*(投资|发展|融资|经营|供应)
      | 增值电信 | 海缆 | 车展 | 购车 | 会员 | 增程版 | 试点
      | 多元化\s*金融 | 资本\s*多元
      | 项目代号 | P\d{6}
      # 36Kr-style tech/finance roundups that leak through via summary keywords
      | 氪星(早|晚)报 | 最前线 | 硬氪首发
      | (Pre-?A|A|B|C|D|天使)\s*轮\s*融资 | 完成\s*[\d.]+\s*(亿|万)\s*元?\s*融资
      | 月包 / 年包 | 付费订阅
    )""",
    re.IGNORECASE | re.VERBOSE,
)

# Title-only noise: junk titles we want to drop unconditionally,
# even from bypass-whitelisted sources.
# - HBS case-study product codes ("Diversity and Authenticity ^ R1802L"):
#   the "^ <CODE>" suffix is HBR's case-store SKU marker, not real research.
# - WordPress "Protected:" prefix (Champions of Change Coalition uses
#   password-protected posts that publish to RSS with no body).
_TITLE_NOISE_PATTERN = re.compile(
    r"""(
        \^\s*[A-Z]{1,3}\d{2,}[A-Z0-9]*\b   # HBS case-store SKU like H03EKK / SMU220 / R1802L
      | ^\s*Protected\s*:\s*$              # WordPress password-protected stub
    )""",
    re.VERBOSE,
)

# Staff-profile pages from competitor sites (Korn Ferry, Mercer etc) often
# slip in via Google News with no real content — just a person's bio.
# Title pattern: "FirstName LastName(s), <Title> ... - <CompanyName>"
# Always applied (even on whitelist-bypassed sources) so they're filtered out.
_STAFF_PROFILE_PATTERN = re.compile(
    r"""^
        [A-ZĞŞİÇÖÜÁÉÍÓÚ][a-zğşıçöüáéíóúA-Z]+        # First name token
        (?:\s+[A-ZĞŞİÇÖÜÁÉÍÓÚ][\w\.\-']+){1,3}      # 1-3 more name tokens
        \s*,\s*                                      # comma after name
        (?:Senior\s+|Associate\s+|Principal\s+|Global\s+|Vice\s+|Managing\s+|North\s+America\s+)*
        (?:Client\s+)?
        (?:Partner|Consultant|Principal|Director|Advisor|Lead|Manager|Officer|Head)
        .* - \s*(?:Korn\s+Ferry|Mercer|Catalyst|BCG|Deloitte|McKinsey|EY|KPMG|PwC|Aon)
    \s*$""",
    re.VERBOSE,
)


def _is_staff_profile(title: str) -> bool:
    """Detect competitor staff-bio pages (e.g., 'Kim Waller, Senior Client Partner - Korn Ferry')."""
    return bool(_STAFF_PROFILE_PATTERN.match(title.strip()))


def _is_title_noise(title: str) -> bool:
    """Detect junk titles (HBS case SKUs, WordPress Protected: stubs)."""
    return bool(_TITLE_NOISE_PATTERN.search(title))


def _is_dei_relevant(item: RawItem) -> bool:
    haystack = f"{item.title} {item.summary}"
    if _NOISE_PATTERNS.search(haystack):
        return False
    return bool(_DEI_KEYWORDS.search(haystack))


def fetch_all(lookback_days: int, max_per_source: int) -> List[RawItem]:
    """Run every registered source and return a flat de-duplicated list,
    filtered down to items that mention DEI-related keywords.

    DEI-specialist sources (is_competitor=True, or explicitly opted in via
    bypass_dei_filter=True) skip the keyword filter — every article they
    publish is presumptively in scope, since the source itself is curated.
    """
    seen: set[str] = set()
    items: List[RawItem] = []
    dropped = 0
    dropped_staff = 0
    dropped_title = 0
    for source in ALL_SOURCES:
        # Whitelist exemption: trust the source's own curation
        bypass = getattr(source, "bypass_dei_filter", False) or getattr(source, "is_competitor", False)
        try:
            for item in source.fetch(lookback_days=lookback_days, limit=max_per_source):
                if item.url in seen:
                    continue
                seen.add(item.url)
                # Always drop staff-profile pages, even from bypass sources
                if _is_staff_profile(item.title):
                    dropped_staff += 1
                    continue
                # Always drop junk titles (HBS SKUs, WordPress Protected: stubs)
                if _is_title_noise(item.title):
                    dropped_title += 1
                    continue
                if not bypass and not _is_dei_relevant(item):
                    dropped += 1
                    continue
                items.append(item)
        except Exception as exc:  # noqa: BLE001 — never let one source kill the run
            from ..utils import get_logger

            get_logger(__name__).warning("Source %s failed: %s", source.name, exc)

    if dropped or dropped_staff or dropped_title:
        from ..utils import get_logger

        get_logger(__name__).info(
            "Pre-filter dropped %d non-DEI + %d staff-profile + %d title-noise; kept %d",
            dropped,
            dropped_staff,
            dropped_title,
            len(items),
        )

    # Resolve Google News redirect URLs to final article URLs, then re-dedupe
    # in case two GN feeds pointed to the same underlying article.
    if items:
        from .url_resolver import resolve_google_news_urls

        url_map = resolve_google_news_urls(it.url for it in items)
        if url_map:
            for it in items:
                if it.url in url_map:
                    it.url = url_map[it.url]
            seen_final: set[str] = set()
            deduped: List[RawItem] = []
            for it in items:
                if it.url in seen_final:
                    continue
                seen_final.add(it.url)
                deduped.append(it)
            items = deduped

    return items


__all__ = ["RawItem", "Source", "fetch_all", "ALL_SOURCES"]
