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
        \b(?: DEI | DEIB | LGBTQ | LGBT )\b
        | diversity | inclusion | inclusive | equity | equality | belonging
        | gender | women | woman
        | racial | racism | \brace\b | ethnic | minority | minorities
        | disability | disabilities | accessib | neurodiver
        | bias | discrimination | harassment | parental | caregiv
        | multicultural | inter-cultural | cross-cultural
        | 多元 | 包容 | 平等 | 公平 | 性别 | 女性 | 残障 | 残疾 | 无障碍
        | 性少数 | 育儿 | 生育 | 照护 | 跨文化 | 反歧视 | 歧视
        | 神经多样 | 多元化 | 共融
    )""",
    re.IGNORECASE | re.VERBOSE,
)

# Exclusion patterns: false positives we want to drop even if a DEI keyword
# was matched. Tuned for noise we have actually observed in the wild.
_NOISE_PATTERNS = re.compile(
    r"""(
        \bprivate\s+equity\b           # finance, not DEI
      | \bequity\s+(market|fund|firm|investment|valuation|partner)
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
    )""",
    re.IGNORECASE | re.VERBOSE,
)


def _is_dei_relevant(item: RawItem) -> bool:
    haystack = f"{item.title} {item.summary}"
    if _NOISE_PATTERNS.search(haystack):
        return False
    return bool(_DEI_KEYWORDS.search(haystack))


def fetch_all(lookback_days: int, max_per_source: int) -> List[RawItem]:
    """Run every registered source and return a flat de-duplicated list,
    filtered down to items that mention DEI-related keywords."""
    seen: set[str] = set()
    items: List[RawItem] = []
    dropped = 0
    for source in ALL_SOURCES:
        try:
            for item in source.fetch(lookback_days=lookback_days, limit=max_per_source):
                if item.url in seen:
                    continue
                seen.add(item.url)
                if not _is_dei_relevant(item):
                    dropped += 1
                    continue
                items.append(item)
        except Exception as exc:  # noqa: BLE001 — never let one source kill the run
            from ..utils import get_logger

            get_logger(__name__).warning("Source %s failed: %s", source.name, exc)

    if dropped:
        from ..utils import get_logger

        get_logger(__name__).info(
            "Pre-filter dropped %d non-DEI items; kept %d",
            dropped,
            len(items),
        )
    return items


__all__ = ["RawItem", "Source", "fetch_all", "ALL_SOURCES"]
