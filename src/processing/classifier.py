"""Client-relevance ranking + pillar grouping helpers (v2).

The analyzer emits per-client-segment relevance scores. This module turns those
scores into:
  - a single weighted "client_score" (used to rank items for daily card / hub)
  - a top-client tag ("此条对哪类客户最有价值")
  - groupings by pillar / by competitor flag (used by the daily card layout
    and the weekly trend report)

Weights come from .env (WEIGHT_MNC_IN_CHINA / WEIGHT_ESG_LISTING /
WEIGHT_GOING_GLOBAL); defaults all 1.0.
"""
from __future__ import annotations

import os
from collections import defaultdict
from typing import Dict, List

from .analyzer import AnalyzedItem


CLIENT_SEGMENTS = ("mnc_in_china", "esg_listing", "going_global")
CLIENT_LABELS_ZH = {
    "mnc_in_china": "在华跨国",
    "esg_listing":  "ESG/上市",
    "going_global": "出海",
}


def _weights() -> Dict[str, float]:
    return {
        "mnc_in_china": float(os.getenv("WEIGHT_MNC_IN_CHINA", "1.0")),
        "esg_listing":  float(os.getenv("WEIGHT_ESG_LISTING",  "1.0")),
        "going_global": float(os.getenv("WEIGHT_GOING_GLOBAL", "1.0")),
    }


def client_score(item: AnalyzedItem, weights: Dict[str, float] | None = None) -> float:
    """Weighted sum of the three client-relevance scores.

    Result range: 0.0 to 5*sum(weights). With default weights of 1.0 each,
    that's 0–15.
    """
    w = weights or _weights()
    return (
        item.relevance_mnc_china  * w["mnc_in_china"]
        + item.relevance_esg_listing * w["esg_listing"]
        + item.relevance_going_global * w["going_global"]
    )


def top_client(item: AnalyzedItem) -> str | None:
    """Return the client segment key with the highest individual relevance score,
    or None if all three are zero. Ties broken by CLIENT_SEGMENTS order."""
    pairs = [
        ("mnc_in_china", item.relevance_mnc_china),
        ("esg_listing",  item.relevance_esg_listing),
        ("going_global", item.relevance_going_global),
    ]
    best_key, best_score = max(pairs, key=lambda x: x[1])
    return best_key if best_score > 0 else None


def rank_by_relevance(
    items: List[AnalyzedItem],
    weights: Dict[str, float] | None = None,
) -> List[AnalyzedItem]:
    """Return items sorted high-to-low by weighted client_score, breaking ties
    with rigor_score then overall_relevance."""
    w = weights or _weights()
    return sorted(
        items,
        key=lambda a: (
            client_score(a, w),
            a.rigor_score,
            a.overall_relevance,
        ),
        reverse=True,
    )


def group_by_pillar(items: List[AnalyzedItem]) -> Dict[str, List[AnalyzedItem]]:
    """Bucket items by pillar. An item may appear in multiple buckets if it has
    multiple pillars. Items with empty pillars list go into 'unclassified'."""
    buckets: Dict[str, List[AnalyzedItem]] = defaultdict(list)
    for a in items:
        if not a.pillars:
            buckets["unclassified"].append(a)
            continue
        for p in a.pillars:
            buckets[p].append(a)
    return dict(buckets)


def split_competitors(
    items: List[AnalyzedItem],
) -> tuple[List[AnalyzedItem], List[AnalyzedItem]]:
    """Return (competitor_items, regular_items)."""
    comp = [a for a in items if a.raw.is_competitor]
    reg = [a for a in items if not a.raw.is_competitor]
    return comp, reg
