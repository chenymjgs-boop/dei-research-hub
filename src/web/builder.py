"""Static-site builder for the DEI research hub.

Produces a tiny, professional, client-facing website from the SQLite database:
    reports/site/
      index.html               — landing/hub page listing every weekly report
      weeks/<week_key>.html    — one page per week
      assets/style.css         — clean stylesheet (copied)

Re-runs are idempotent — the entire `site/` directory is regenerated from DB.
Run with: python -m src.web.builder   or   python main.py site
"""
from __future__ import annotations

import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import List

import markdown as md_lib
from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..config import SETTINGS
from ..processing.analyzer import AnalyzedItem
from ..storage import Database
from ..utils import get_logger

LOGGER = get_logger(__name__)

WEB_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"


def build_site(out_dir: Path | None = None) -> Path:
    """Render the cumulative hub site from DB. Returns the output directory."""
    out_dir = Path(out_dir) if out_dir else SETTINGS.reports_dir / "site"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "weeks").mkdir(exist_ok=True)
    (out_dir / "assets").mkdir(exist_ok=True)

    # Copy static assets
    for f in STATIC_DIR.iterdir():
        shutil.copy2(f, out_dir / "assets" / f.name)

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "xml"]),
    )

    db = Database(SETTINGS.db_path)
    weeks_raw = db.list_weekly_reports()
    stats = db.stats()
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")

    # Build per-week pages and collect index summaries in a single pass
    index_weeks = []
    week_tpl = env.get_template("week.html")
    for w in weeks_raw:
        items = db.fetch_items_by_hashes(w["item_hashes"])
        ctx_items = [_item_ctx(it) for it in items]
        narrative_html = md_lib.markdown(
            w["narrative_md"], extensions=["extra", "sane_lists"]
        )

        week_ctx = {
            "week_key": w["week_key"],
            "start_date": w["start_date"],
            "end_date": w["end_date"],
            "title": w["title"],
            "item_count": len(ctx_items),
            "narrative_html": narrative_html,
        }

        page_html = week_tpl.render(
            week=week_ctx,
            items=ctx_items,
            assets="../assets/",
            root="../",
            generated_at=generated_at,
        )
        (out_dir / "weeks" / f"{w['week_key']}.html").write_text(
            page_html, encoding="utf-8"
        )

        # Compute top topics from this week's items for the index card
        topic_counter: Counter[str] = Counter()
        for it in items:
            topic_counter.update(it.topics)
        top_topics = [t for t, _ in topic_counter.most_common(3)]

        # First non-heading paragraph as the index preview
        preview = _first_paragraph(w["narrative_md"], max_chars=180)

        index_weeks.append(
            {
                "week_key": w["week_key"],
                "start_date": w["start_date"],
                "end_date": w["end_date"],
                "title": w["title"],
                "item_count": len(items),
                "top_topics": top_topics,
                "preview": preview,
            }
        )

    # Build index
    index_tpl = env.get_template("index.html")
    index_html = index_tpl.render(
        weeks=index_weeks,
        total_items=stats["total_items"],
        total_sources=stats["total_sources"],
        total_weeks=stats["total_weeks"],
        assets="assets/",
        root="",
        generated_at=generated_at,
    )
    (out_dir / "index.html").write_text(index_html, encoding="utf-8")

    LOGGER.info("Site built at %s (%d weeks, %d items)",
                out_dir, len(index_weeks), stats["total_items"])
    return out_dir


def _item_ctx(item: AnalyzedItem) -> dict:
    pub = item.raw.published_at
    return {
        "title": item.raw.title,
        "url": item.raw.url,
        "source_name": item.raw.source_name,
        "source_category": item.raw.source_category,
        "published_at_label": pub.strftime("%Y-%m-%d") if pub else "未知",
        "evidence_type": item.evidence_type,
        "zh_summary": item.zh_summary,
        "china_implication": item.china_implication,
        "topics": item.topics,
        "industries": item.industries,
        "rigor_score": item.rigor_score,
        "relevance_score": item.relevance_score,
    }


def _first_paragraph(markdown_text: str, max_chars: int = 180) -> str:
    for line in markdown_text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("-") or s.startswith("*"):
            continue
        if len(s) > max_chars:
            return s[:max_chars] + "…"
        return s
    return ""


if __name__ == "__main__":
    build_site()
