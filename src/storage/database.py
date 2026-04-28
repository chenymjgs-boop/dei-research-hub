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


SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    hash TEXT PRIMARY KEY,
    url TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    source_name TEXT,
    source_category TEXT,
    region TEXT,
    published_at TEXT,
    fetched_at TEXT NOT NULL,
    analyzed_at TEXT,
    en_summary TEXT,
    zh_summary TEXT,
    china_implication TEXT,
    key_takeaways_json TEXT,
    topics_json TEXT,
    industries_json TEXT,
    evidence_type TEXT,
    rigor_score INTEGER,
    relevance_score INTEGER,
    delivered INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_items_fetched_at ON items(fetched_at);
CREATE INDEX IF NOT EXISTS idx_items_delivered ON items(delivered);

CREATE TABLE IF NOT EXISTS weekly_reports (
    week_key TEXT PRIMARY KEY,        -- e.g. "2026-W17"
    start_date TEXT NOT NULL,         -- YYYY-MM-DD
    end_date TEXT NOT NULL,           -- YYYY-MM-DD
    title TEXT NOT NULL,
    narrative_md TEXT NOT NULL,       -- raw markdown from Claude
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
                    hash, url, title, source_name, source_category, region,
                    published_at, fetched_at, analyzed_at,
                    en_summary, zh_summary, china_implication,
                    key_takeaways_json, topics_json, industries_json,
                    evidence_type, rigor_score, relevance_score, delivered
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    r.hash,
                    r.url,
                    r.title,
                    r.source_name,
                    r.source_category,
                    r.region,
                    r.published_at.isoformat() if r.published_at else None,
                    now_utc().isoformat(),
                    analyzed.analyzed_at.isoformat() if analyzed.analyzed_at else None,
                    analyzed.en_summary,
                    analyzed.zh_summary,
                    analyzed.china_implication,
                    json.dumps(analyzed.key_takeaways, ensure_ascii=False),
                    json.dumps(analyzed.topics, ensure_ascii=False),
                    json.dumps(analyzed.industries, ensure_ascii=False),
                    analyzed.evidence_type,
                    analyzed.rigor_score,
                    analyzed.relevance_score,
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
                "ORDER BY relevance_score DESC, rigor_score DESC, fetched_at DESC",
                (cutoff,),
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
                f"ORDER BY relevance_score DESC, rigor_score DESC",
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
    from ..sources.base import RawItem
    from ..utils import parse_date

    raw = RawItem(
        title=row["title"],
        url=row["url"],
        source_name=row["source_name"] or "",
        source_category=row["source_category"] or "",
        published_at=parse_date(row["published_at"]),
        summary="",
        region=row["region"] or "global",
    )
    return AnalyzedItem(
        raw=raw,
        en_summary=row["en_summary"] or "",
        zh_summary=row["zh_summary"] or "",
        key_takeaways=json.loads(row["key_takeaways_json"] or "[]"),
        china_implication=row["china_implication"] or "",
        topics=json.loads(row["topics_json"] or "[]"),
        industries=json.loads(row["industries_json"] or "[]"),
        evidence_type=row["evidence_type"] or "",
        rigor_score=row["rigor_score"] or 0,
        relevance_score=row["relevance_score"] or 0,
        analyzed_at=parse_date(row["analyzed_at"]),
    )
