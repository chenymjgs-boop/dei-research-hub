"""Lightweight one-shot research report.

Pipeline:
    fetch_all sources
    → cap by relevance heuristics
    → LLM analyze
    → write markdown to reports/report-YYYY-MM-DD-HHMM.md

No database, no Feishu. Useful for quick local trials without setting up
the full delivery pipeline.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Iterable, List

from ..config import SETTINGS
from ..processing import Analyzer
from ..processing.analyzer import AnalyzedItem
from ..sources import fetch_all
from ..utils import get_logger

LOGGER = get_logger(__name__)


def run_report() -> Path:
    LOGGER.info("=== DEI Research Assistant · Report (markdown only) ===")

    raw = fetch_all(
        lookback_days=SETTINGS.lookback_days,
        max_per_source=SETTINGS.max_items_per_source,
    )
    LOGGER.info("Fetched %d items", len(raw))

    items = _balance_by_category(raw, SETTINGS.max_items_per_run)
    LOGGER.info("Capped to %d items for analysis", len(items))

    if not items:
        LOGGER.warning("No items to analyze — writing an empty report anyway")

    analyzer = Analyzer()
    analyzed = analyzer.analyze_batch(items) if items else []
    valid = [a for a in analyzed if a.is_valid()]
    failed = [a for a in analyzed if not a.is_valid()]
    LOGGER.info("Analyzed: %d valid / %d failed", len(valid), len(failed))

    out_path = SETTINGS.reports_dir / _filename()
    out_path.write_text(_render_markdown(valid, failed), encoding="utf-8")
    LOGGER.info("Wrote %s", out_path)
    return out_path


def _filename() -> str:
    return f"report-{datetime.now().strftime('%Y-%m-%d-%H%M')}.md"


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


def _render_markdown(valid: List[AnalyzedItem], failed: List[AnalyzedItem]) -> str:
    lines: List[str] = []
    today = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines.append(f"# DEI 研究简报 · {today}")
    lines.append("")
    lines.append(f"- 有效分析：**{len(valid)}** 条")
    lines.append(f"- 分析失败：{len(failed)} 条")
    lines.append("")

    by_cat: dict[str, List[AnalyzedItem]] = {}
    for a in valid:
        by_cat.setdefault(a.raw.source_category, []).append(a)

    cat_order = ["academic", "consulting", "international", "media", "china"]
    cat_label = {
        "academic": "学术与研究",
        "consulting": "咨询机构",
        "international": "国际组织",
        "media": "行业媒体",
        "china": "中国本土",
    }
    for cat in cat_order:
        items = by_cat.get(cat) or []
        if not items:
            continue
        lines.append(f"## {cat_label.get(cat, cat)}")
        lines.append("")
        for a in sorted(items, key=lambda x: -x.relevance_score):
            lines.extend(_render_item(a))
            lines.append("")

    if failed:
        lines.append("---")
        lines.append("")
        lines.append("## 分析失败的条目（保留原始链接以便人工查看）")
        lines.append("")
        for a in failed:
            err = (a.error or "")[:120]
            lines.append(f"- [{a.raw.title}]({a.raw.url}) · {a.raw.source_name} · `{err}`")
        lines.append("")

    return "\n".join(lines)


def _render_item(a: AnalyzedItem) -> Iterable[str]:
    raw = a.raw
    pub = raw.published_at.strftime("%Y-%m-%d") if raw.published_at else "未知"
    out = [
        f"### [{raw.title}]({raw.url})",
        "",
        f"**来源**：{raw.source_name} · **发布**：{pub} · "
        f"**严谨度** {a.rigor_score}/5 · **相关性** {a.relevance_score}/5 · "
        f"**证据类型**：{a.evidence_type or '—'}",
        "",
    ]
    if a.zh_summary:
        out.append("**中文摘要**")
        out.append("")
        out.append(a.zh_summary)
        out.append("")
    if a.key_takeaways:
        out.append("**关键要点**")
        for kt in a.key_takeaways:
            out.append(f"- {kt}")
        out.append("")
    if a.china_implication:
        out.append("**对中国企业的启示**")
        out.append("")
        out.append(a.china_implication)
        out.append("")
    if a.topics or a.industries:
        tags = []
        if a.topics:
            tags.append("话题：" + " / ".join(a.topics))
        if a.industries:
            tags.append("行业：" + " / ".join(a.industries))
        out.append("`" + " · ".join(tags) + "`")
    if a.en_summary:
        out.append("")
        out.append("<details><summary>English summary</summary>")
        out.append("")
        out.append(a.en_summary)
        out.append("")
        out.append("</details>")
    return out


if __name__ == "__main__":
    run_report()
