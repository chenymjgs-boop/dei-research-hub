"""Daily orchestrator.

Pipeline:
    fetch_all sources
    → dedup against DB
    → cap by relevance heuristics
    → Claude analyze
    → save to SQLite
    → push to Feishu (Bitable + daily card)
"""
from __future__ import annotations

from datetime import datetime, timezone

from ..config import SETTINGS
from ..delivery import FeishuClient
from ..processing import Analyzer
from ..sources import fetch_all
from ..storage import Database
from ..utils import get_logger

LOGGER = get_logger(__name__)


def run_daily() -> None:
    LOGGER.info("=== DEI Research Assistant · Daily run ===")
    db = Database(SETTINGS.db_path)

    # 1. Fetch
    raw = fetch_all(
        lookback_days=SETTINGS.lookback_days,
        max_per_source=SETTINGS.max_items_per_source,
    )
    LOGGER.info("Fetched %d raw items from %d sources", len(raw), _source_count(raw))

    # 2. Dedup
    new_items = db.filter_new(raw)
    LOGGER.info("After dedup: %d new items", len(new_items))

    # 3. Cap by max_items_per_run, biased toward variety across categories
    new_items = _balance_by_category(new_items, SETTINGS.max_items_per_run)
    LOGGER.info("Capped to %d items for analysis", len(new_items))
    if not new_items:
        LOGGER.info("Nothing new today — exiting")
        return

    # 4. Analyze with Claude
    analyzer = Analyzer()
    analyzed = analyzer.analyze_batch(new_items)
    valid = [a for a in analyzed if a.is_valid()]
    LOGGER.info("Analyzed: %d valid / %d total", len(valid), len(analyzed))

    # 5. Persist
    for a in valid:
        db.save_analyzed(a)

    # 6. Deliver to Feishu
    feishu = FeishuClient()
    try:
        feishu.push_to_bitable(valid)
    except Exception as exc:  # noqa: BLE001
        LOGGER.error("Bitable push failed: %s", exc)
    try:
        date_label = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
        feishu.send_daily_card(valid, date_label)
    except Exception as exc:  # noqa: BLE001
        LOGGER.error("Chat card push failed: %s", exc)

    db.mark_delivered(a.raw.hash for a in valid)
    LOGGER.info("=== Daily run complete ===")


def _source_count(items) -> int:
    return len({i.source_name for i in items})


def _balance_by_category(items, cap: int):
    """Round-robin across categories so one chatty source doesn't drown out signal."""
    buckets: dict[str, list] = {}
    for it in items:
        buckets.setdefault(it.source_category, []).append(it)

    result: list = []
    while len(result) < cap and any(buckets.values()):
        for cat in list(buckets.keys()):
            if not buckets[cat]:
                continue
            result.append(buckets[cat].pop(0))
            if len(result) >= cap:
                break
    return result


if __name__ == "__main__":
    run_daily()
