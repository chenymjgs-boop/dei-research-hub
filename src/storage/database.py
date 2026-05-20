"""SQLite-based dedup and history.

Tracks every URL we've ever processed so the daily run only sends *new* items
to the LLM and to Feishu.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, List

from ..processing.analyzer import AnalyzedItem
from ..sources.base import RawItem


# v2 schema. New databases will be created with all v2 columns directly.
# Existing v1 databases are migrated by scripts/migrate_v1_to_v2.py — that
# script handles the rename of `china_implication` → `implication_mnc_china`
# and `relevance_score` → `overall_relevance`, plus the additive columns.
SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    hash TEXT PRIMARY KEY,
    url TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    title_zh TEXT,
    source_name TEXT,
    source_category TEXT,
    source_subtype TEXT,
    region TEXT,
    is_competitor INTEGER DEFAULT 0,
    published_at TEXT,
    fetched_at TEXT NOT NULL,
    analyzed_at TEXT,
    en_summary TEXT,
    zh_summary TEXT,
    key_takeaways_json TEXT,
    pillars_json TEXT,
    implication_mnc_china TEXT,
    implication_esg_listing TEXT,
    implication_going_global TEXT,
    relevance_mnc_china INTEGER,
    relevance_esg_listing INTEGER,
    relevance_going_global INTEGER,
    competitor_intelligence TEXT,
    topics_json TEXT,
    industries_json TEXT,
    evidence_type TEXT,
    rigor_score INTEGER,
    overall_relevance INTEGER,
    stance TEXT,                                   -- v2.1: backlash/persist/mainstream/controversy
    delivered INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_items_fetched_at ON items(fetched_at);
CREATE INDEX IF NOT EXISTS idx_items_delivered ON items(delivered);
CREATE INDEX IF NOT EXISTS idx_items_competitor ON items(is_competitor);

CREATE TABLE IF NOT EXISTS weekly_reports (
    week_key TEXT PRIMARY KEY,        -- e.g. "2026-W17"
    start_date TEXT NOT NULL,         -- YYYY-MM-DD
    end_date TEXT NOT NULL,           -- YYYY-MM-DD
    title TEXT NOT NULL,
    narrative_md TEXT NOT NULL,       -- raw markdown from the configured LLM
    item_hashes_json TEXT NOT NULL,   -- list of item hashes covered
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_weekly_end_date ON weekly_reports(end_date DESC);
"""


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ---- dedup ----

    def filter_new(self, items: Iterable[RawItem]) -> List[RawItem]:
        items = list(items)
        if not items:
            return []
        with self._conn() as c:
            placeholders = ",".join("?" * len(items))
            rows = c.execute(
                f"SELECT hash FROM items WHERE hash IN ({placeholders})",
                [i.hash for i in items],
            ).fetchall()
            seen = {r["hash"] for r in rows}
        return [i for i in items if i.hash not in seen]

    # ---- persist ----

    def save_analyzed(self, analyzed: AnalyzedItem) -> None:
        from ..utils import now_utc

        r = analyzed.raw
        with self._conn() as c:
            c.execute(
                """
                INSERT OR REPLACE INTO items (
                    hash, url, title, title_zh,
                    source_name, source_category, source_subtype,
                    region, is_competitor,
                    published_at, fetched_at, analyzed_at,
                    en_summary, zh_summary, key_takeaways_json,
                    pillars_json,
                    implication_mnc_china, implication_esg_listing, implication_going_global,
                    relevance_mnc_china, relevance_esg_listing, relevance_going_global,
                    competitor_intelligence,
                    topics_json, industries_json,
                    evidence_type, rigor_score, overall_relevance,
                    stance,
                    delivered
                ) VALUES (?, ?, ?, ?,
                          ?, ?, ?,
                          ?, ?,
                          ?, ?, ?,
                          ?, ?, ?,
                          ?,
                          ?, ?, ?,
                          ?, ?, ?,
                          ?,
                          ?, ?,
                          ?, ?, ?,
                          ?,
                          0)
                """,
                (
                    r.hash,
                    r.url,
                    r.title,
                    analyzed.title_zh,
                    r.source_name,
                    r.source_category,
                    r.source_subtype,
                    r.region,
                    1 if r.is_competitor else 0,
                    r.published_at.isoformat() if r.published_at else None,
                    now_utc().isoformat(),
                    analyzed.analyzed_at.isoformat() if analyzed.analyzed_at else None,
                    analyzed.en_summary,
                    analyzed.zh_summary,
                    json.dumps(analyzed.key_takeaways, ensure_ascii=False),
                    json.dumps(analyzed.pillars, ensure_ascii=False),
                    analyzed.implication_mnc_china,
                    analyzed.implication_esg_listing,
                    analyzed.implication_going_global,
                    analyzed.relevance_mnc_china,
                    analyzed.relevance_esg_listing,
                    analyzed.relevance_going_global,
                    analyzed.competitor_intelligence,
                    json.dumps(analyzed.topics, ensure_ascii=False),
                    json.dumps(analyzed.industries, ensure_ascii=False),
                    analyzed.evidence_type,
                    analyzed.rigor_score,
                    analyzed.overall_relevance,
                    analyzed.stance or "",
                ),
            )

    def mark_delivered(self, hashes: Iterable[str]) -> None:
        hashes = list(hashes)
        if not hashes:
            return
        with self._conn() as c:
            c.executemany(
                "UPDATE items SET delivered = 1 WHERE hash = ?",
                [(h,) for h in hashes],
            )

    # ---- query ----

    def fetch_recent(self, days: int) -> List[AnalyzedItem]:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM items WHERE fetched_at >= ? AND zh_summary IS NOT NULL "
                "ORDER BY overall_relevance DESC, rigor_score DESC, fetched_at DESC",
                (cutoff,),
            ).fetchall()
        return [_row_to_analyzed(r) for r in rows]

    def fetch_latest(self, limit: int = 30) -> List[AnalyzedItem]:
        """Return the latest analyzed items by collection time."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM items WHERE zh_summary IS NOT NULL "
                "ORDER BY fetched_at DESC, overall_relevance DESC, rigor_score DESC "
                "LIMIT ?",
                (limit,),
            ).fetchall()
        return [_row_to_analyzed(r) for r in rows]

    # ---- weekly reports (cumulative archive for the web hub) ----

    def save_weekly_report(
        self,
        week_key: str,
        start_date: str,
        end_date: str,
        title: str,
        narrative_md: str,
        item_hashes: Iterable[str],
    ) -> None:
        from ..utils import now_utc

        with self._conn() as c:
            c.execute(
                """
                INSERT OR REPLACE INTO weekly_reports
                (week_key, start_date, end_date, title, narrative_md,
                 item_hashes_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    week_key,
                    start_date,
                    end_date,
                    title,
                    narrative_md,
                    json.dumps(list(item_hashes)),
                    now_utc().isoformat(),
                ),
            )

    def list_weekly_reports(self) -> List[dict]:
        """Return all weekly reports, newest first."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM weekly_reports ORDER BY end_date DESC, week_key DESC"
            ).fetchall()
        return [
            {
                "week_key": r["week_key"],
                "start_date": r["start_date"],
                "end_date": r["end_date"],
                "title": r["title"],
                "narrative_md": r["narrative_md"],
                "item_hashes": json.loads(r["item_hashes_json"] or "[]"),
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    def fetch_items_by_hashes(self, hashes: Iterable[str]) -> List[AnalyzedItem]:
        hashes = list(hashes)
        if not hashes:
            return []
        with self._conn() as c:
            placeholders = ",".join("?" * len(hashes))
            rows = c.execute(
                f"SELECT * FROM items WHERE hash IN ({placeholders}) "
                f"AND zh_summary IS NOT NULL "
                f"ORDER BY overall_relevance DESC, rigor_score DESC",
                hashes,
            ).fetchall()
        return [_row_to_analyzed(r) for r in rows]

    def stats(self) -> dict:
        """Aggregate counts for the index page."""
        with self._conn() as c:
            total_items = c.execute(
                "SELECT COUNT(*) FROM items WHERE zh_summary IS NOT NULL"
            ).fetchone()[0]
            total_sources = c.execute(
                "SELECT COUNT(DISTINCT source_name) FROM items "
                "WHERE source_name IS NOT NULL AND source_name != ''"
            ).fetchone()[0]
            total_weeks = c.execute(
                "SELECT COUNT(*) FROM weekly_reports"
            ).fetchone()[0]
        return {
            "total_items": total_items,
            "total_sources": total_sources,
            "total_weeks": total_weeks,
        }


def _row_to_analyzed(row: sqlite3.Row) -> AnalyzedItem:
    """Reconstruct an AnalyzedItem from a v2 schema row.

    Handles graceful absence of new v2 columns (sqlite3.Row raises IndexError
    on missing keys, so we use _safe_get).
    """
    from ..sources.base import RawItem
    from ..utils import parse_date

    raw = RawItem(
        title=row["title"],
        url=row["url"],
        source_name=row["source_name"] or "",
        source_category=row["source_category"] or "",
        source_subtype=_safe_get(row, "source_subtype") or "",
        published_at=parse_date(row["published_at"]),
        summary="",
        region=row["region"] or "global",
        is_competitor=bool(_safe_get(row, "is_competitor") or 0),
    )
    return AnalyzedItem(
        raw=raw,
        title_zh=_safe_get(row, "title_zh") or "",
        en_summary=row["en_summary"] or "",
        zh_summary=row["zh_summary"] or "",
        key_takeaways=json.loads(row["key_takeaways_json"] or "[]"),
        pillars=json.loads(_safe_get(row, "pillars_json") or "[]"),
        implication_mnc_china=_safe_get(row, "implication_mnc_china") or "",
        implication_esg_listing=_safe_get(row, "implication_esg_listing") or "",
        implication_going_global=_safe_get(row, "implication_going_global") or "",
        relevance_mnc_china=_safe_get(row, "relevance_mnc_china") or 0,
        relevance_esg_listing=_safe_get(row, "relevance_esg_listing") or 0,
        relevance_going_global=_safe_get(row, "relevance_going_global") or 0,
        competitor_intelligence=_safe_get(row, "competitor_intelligence") or "",
        topics=json.loads(row["topics_json"] or "[]"),
        industries=json.loads(row["industries_json"] or "[]"),
        evidence_type=row["evidence_type"] or "",
        rigor_score=row["rigor_score"] or 0,
        overall_relevance=_safe_get(row, "overall_relevance") or 0,
        stance=_safe_get(row, "stance") or "",
        analyzed_at=parse_date(row["analyzed_at"]),
    )


def _safe_get(row: sqlite3.Row, key: str):
    """sqlite3.Row.__getitem__ raises IndexError for missing columns; we want None."""
    try:
        return row[key]
    except (IndexError, KeyError):
        return None
