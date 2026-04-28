"""Weekly trend report orchestrator.

Reads the last 7 days of analyzed items from SQLite, asks Claude to synthesize a
trend report, persists the report to the `weekly_reports` table, regenerates the
cumulative static-site hub, and notifies Feishu.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..config import SETTINGS
from ..delivery import FeishuClient
from ..processing import Analyzer
from ..storage import Database
from ..utils import get_logger
from ..web.builder import build_site

LOGGER = get_logger(__name__)


def run_weekly() -> None:
    LOGGER.info("=== DEI Research Assistant · Weekly run ===")
    db = Database(SETTINGS.db_path)
    items = db.fetch_recent(days=7)
    LOGGER.info("Found %d analyzed items in the last 7 days", len(items))
    if len(items) < 3:
        LOGGER.info(
            "Not enough items for a weekly report — rebuilding hub anyway"
        )
        # Still regenerate the site so any prior weeks remain visible
        site_dir = build_site()
        LOGGER.info("Hub rebuilt → %s", site_dir / "index.html")
        return

    end_dt = datetime.now(timezone.utc).astimezone()
    start_dt = end_dt - timedelta(days=7)
    start_label = start_dt.strftime("%Y-%m-%d")
    end_label = end_dt.strftime("%Y-%m-%d")
    iso_year, iso_week, _ = end_dt.isocalendar()
    week_key = f"{iso_year}-W{iso_week:02d}"
    title = f"DEI 全球研究周报 · {start_label} → {end_label}"

    analyzer = Analyzer()
    report_md = analyzer.weekly_trend_report(items, start_label, end_label)

    # Persist to DB so the hub can re-render any historical week from scratch
    db.save_weekly_report(
        week_key=week_key,
        start_date=start_label,
        end_date=end_label,
        title=title,
        narrative_md=report_md,
        item_hashes=[i.raw.hash for i in items],
    )
    LOGGER.info("Weekly report persisted (week_key=%s, %d items)",
                week_key, len(items))

    # Save the markdown locally as well (useful for archive / debugging)
    out_path = SETTINGS.reports_dir / f"weekly-{end_label}.md"
    out_path.write_text(report_md, encoding="utf-8")

    # Regenerate the cumulative hub site
    site_dir = build_site()
    LOGGER.info("Hub rebuilt → %s", site_dir / "index.html")

    # Push to Feishu (chat ping; doc creation is optional)
    feishu = FeishuClient()
    doc_url = None
    try:
        doc_url = feishu.create_doc_with_markdown(title, report_md)
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("Doc creation failed: %s", exc)

    try:
        notify = (
            f"📊 **{title}** 已生成\n\n"
            f"覆盖时间：{start_label} → {end_label}\n"
            f"分析条目：{len(items)} 篇\n"
        )
        if doc_url:
            notify += f"📄 飞书云文档：{doc_url}\n"
        notify += f"\n本地 Hub：file://{site_dir.resolve()}/index.html\n"
        notify += f"本周 URL：weeks/{week_key}.html\n"
        feishu.send_text(notify)
    except Exception as exc:  # noqa: BLE001
        LOGGER.error("Weekly chat push failed: %s", exc)

    LOGGER.info("=== Weekly run complete ===")


if __name__ == "__main__":
    run_weekly()
