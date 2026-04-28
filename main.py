"""CLI entry point.

Usage:
    python main.py daily        # daily fetch + analyze + push to Feishu + DB
    python main.py weekly       # weekly trend synthesis + hub rebuild
    python main.py preview      # fetch only — no LLM, no push (for source debugging)
    python main.py report       # fetch + LLM analyze → markdown file (no DB, no Feishu)
    python main.py site         # rebuild the static hub from DB only (no LLM, no fetch)
"""
from __future__ import annotations

import argparse
import sys

from src.config import SETTINGS
from src.sources import fetch_all
from src.tasks.daily import run_daily
from src.tasks.report import run_report
from src.tasks.weekly import run_weekly
from src.utils import get_logger
from src.web import build_site

LOGGER = get_logger("dei.main")


def cmd_preview() -> None:
    items = fetch_all(
        lookback_days=SETTINGS.lookback_days,
        max_per_source=SETTINGS.max_items_per_source,
    )
    print(f"\nFetched {len(items)} items.\n")
    by_cat: dict[str, int] = {}
    for it in items:
        by_cat[it.source_category] = by_cat.get(it.source_category, 0) + 1
    for cat, n in sorted(by_cat.items()):
        print(f"  {cat:14s} {n}")
    print()
    for it in items[:15]:
        print(f"- [{it.source_name}] {it.title[:90]}")
        print(f"  {it.url}")


def main() -> int:
    p = argparse.ArgumentParser(description="DEI Research Assistant")
    p.add_argument("command", choices=["daily", "weekly", "preview", "report", "site"])
    args = p.parse_args()

    if args.command == "daily":
        run_daily()
    elif args.command == "weekly":
        run_weekly()
    elif args.command == "preview":
        cmd_preview()
    elif args.command == "report":
        path = run_report()
        print(f"\nReport written to: {path}")
    elif args.command == "site":
        out = build_site()
        print(f"\nSite built at: {out}/index.html")
    return 0


if __name__ == "__main__":
    sys.exit(main())
