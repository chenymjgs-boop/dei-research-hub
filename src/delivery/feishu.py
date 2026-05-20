"""Feishu (Lark) delivery layer.

Two channels:
1) Bitable (多维表格)  — long-term searchable knowledge base; one row per item
2) Chat message       — daily/weekly digest card pushed to a group chat

Optional:
3) Docs (云文档) — for full weekly/monthly trend reports as Markdown.

Required Feishu permissions on the self-built app:
    bitable:app, bitable:app:readonly  (write to Bitable)
    im:message, im:message:send_as_bot  (send chat messages)
    docx:document  (create docs — only needed for trend reports)
"""
from __future__ import annotations

import json
import time
from typing import Dict, List, Optional

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from ..config import SETTINGS
from ..processing.analyzer import AnalyzedItem
from ..utils import get_logger

LOGGER = get_logger(__name__)
BASE = "https://open.feishu.cn/open-apis"


class FeishuClient:
    def __init__(self) -> None:
        self.app_id = SETTINGS.feishu_app_id
        self.app_secret = SETTINGS.feishu_app_secret
        self._token: Optional[str] = None
        self._token_expires: float = 0.0

    # ---------- auth ----------

    def _get_token(self) -> str:
        if self._token and time.time() < self._token_expires - 60:
            return self._token
        r = requests.post(
            f"{BASE}/auth/v3/tenant_access_token/internal",
            json={"app_id": self.app_id, "app_secret": self.app_secret},
            timeout=20,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("code") != 0:
            raise RuntimeError(f"Feishu auth failed: {data}")
        self._token = data["tenant_access_token"]
        self._token_expires = time.time() + data.get("expire", 7200)
        return self._token

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self._get_token()}",
            "Content-Type": "application/json; charset=utf-8",
        }

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8), reraise=True)
    def _post(self, path: str, payload: dict) -> dict:
        r = requests.post(f"{BASE}{path}", headers=self._headers(), json=payload, timeout=30)
        r.raise_for_status()
        data = r.json()
        if data.get("code") != 0:
            raise RuntimeError(f"Feishu API error {path}: {data}")
        return data

    # ---------- Bitable ----------

    def push_to_bitable(self, items: List[AnalyzedItem]) -> int:
        """Append every analyzed item as a row to the configured v2 Bitable.

        v2 field names (must exist in the Bitable; run scripts/migrate_v1_to_v2.py first):
            标题, 链接, 来源, 来源类别, 来源地域, 来源子类型, 发布日期, 收录日期,
            中文标题, 英文摘要, 中文摘要, 关键要点,
            板块, 是否竞品,
            对跨国在华客户的启示, 对中国ESG/上市客户的启示, 对中国企业出海客户的启示,
            在华跨国相关度, ESG上市相关度, 出海相关度,
            竞品情报, 话题, 行业, 证据类型, 严谨度, 综合相关性
        """
        app_token = SETTINGS.feishu_bitable_app_token
        table_id = SETTINGS.feishu_bitable_table_id
        if not app_token or not table_id:
            LOGGER.warning("Bitable app_token/table_id not configured — skipping push")
            return 0

        records = []
        for a in items:
            if not a.is_valid():
                continue
            r = a.raw
            fields = {
                "标题": r.title,
                "链接": {"link": r.url, "text": r.title[:50]},
                "来源": r.source_name,
                "来源类别": r.source_category,
                "来源地域": r.region,
                "中文标题": a.title_zh,
                "英文摘要": a.en_summary,
                "中文摘要": a.zh_summary,
                "关键要点": "\n".join(f"• {t}" for t in a.key_takeaways),
                "板块": a.pillars,
                "是否竞品": "是" if r.is_competitor else "否",
                "对跨国在华客户的启示": a.implication_mnc_china,
                "对中国ESG/上市客户的启示": a.implication_esg_listing,
                "对中国企业出海客户的启示": a.implication_going_global,
                "在华跨国相关度": a.relevance_mnc_china,
                "ESG上市相关度": a.relevance_esg_listing,
                "出海相关度": a.relevance_going_global,
                "竞品情报": a.competitor_intelligence,
                "话题": a.topics,
                "行业": a.industries,
                "证据类型": a.evidence_type,
                "严谨度": a.rigor_score,
                "综合相关性": a.overall_relevance,
            }
            if a.stance:
                fields["立场"] = a.stance
            if r.source_subtype:
                fields["来源子类型"] = r.source_subtype
            if r.published_at:
                fields["发布日期"] = int(r.published_at.timestamp() * 1000)
            if a.analyzed_at:
                fields["收录日期"] = int(a.analyzed_at.timestamp() * 1000)
            records.append({"fields": fields})

        if not records:
            return 0

        # Batch in groups of 100 (Bitable limit is 1000 but smaller is safer)
        sent = 0
        for batch in _chunks(records, 100):
            self._post(
                f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_create",
                {"records": batch},
            )
            sent += len(batch)
        LOGGER.info("Pushed %d rows to Feishu Bitable", sent)
        return sent

    # ---------- Chat message (interactive card) ----------

    def send_daily_card(self, items: List[AnalyzedItem], date_label: str) -> bool:
        """Send a Feishu interactive card with the day's items grouped by pillar + competitor.

        v2 layout:
          - Header counters per pillar
          - Section 🌍 Global Frontier (pillar=global)
          - Section 🏢 MNC in China (pillar=mnc_china)
          - Section 🚀 China Going Global (pillar=china_going_global)
          - Section ⚠️ Competitor Watch (any item with raw.is_competitor=True)
        Each item shows three client-relevance scores so the consultant can
        spot which client segment to surface it to.
        """
        from ..processing.classifier import (
            rank_by_relevance,
        )

        chat_id = SETTINGS.feishu_chat_id
        if not chat_id:
            LOGGER.info("FEISHU_CHAT_ID not set — skipping chat push")
            return False
        valid_items = [a for a in items if a.is_valid()]
        if not valid_items:
            return False

        ranked = rank_by_relevance(valid_items)
        card = self._build_daily_card(ranked, date_label)
        self._post(
            "/im/v1/messages?receive_id_type=chat_id",
            {
                "receive_id": chat_id,
                "msg_type": "interactive",
                "content": json.dumps(card, ensure_ascii=False),
            },
        )
        LOGGER.info("Sent daily card with %d items to chat %s", len(ranked), chat_id)
        return True

    def _build_daily_card(self, items: List[AnalyzedItem], date_label: str) -> dict:
        from ..processing.classifier import (
            client_score,
        )

        # Daily chat cards should show each item exactly once. Analyzer pillars
        # are multi-select, so grouping directly by every pillar duplicates
        # articles across sections. Competitors get their own section; regular
        # items are assigned to one primary pillar for the digest.
        by_section, comp_items = _group_daily_card_sections(items)

        # Header summary
        elements: list[dict] = [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": (
                        f"**今日 DEI 情报 · {date_label}**\n"
                        f"共 **{len(items)}** 条精选 · "
                        f"全球前沿 {len(by_section.get('global', []))} · "
                        f"在华跨国 {len(by_section.get('mnc_china', []))} · "
                        f"中国出海 {len(by_section.get('china_going_global', []))} · "
                        f"竞品 {len(comp_items)}"
                    ),
                },
            },
            {"tag": "hr"},
        ]

        # Pillar sections (top 3-4 per pillar by client_score)
        pillar_blocks = [
            ("🌍 全球前沿 · Global Frontier", "global"),
            ("🏢 在华跨国 · MNC in China",     "mnc_china"),
            ("🚀 中国出海 · Going Global",     "china_going_global"),
        ]
        for header_text, pkey in pillar_blocks:
            section = sorted(
                by_section.get(pkey, []),
                key=lambda a: (client_score(a), a.rigor_score),
                reverse=True,
            )[:3]  # top 3 per pillar
            if not section:
                continue
            elements.append({
                "tag": "div",
                "text": {"tag": "lark_md", "content": f"**{header_text}**"},
            })
            for a in section:
                elements.append(_format_item_card_block(a))
            elements.append({"tag": "hr"})

        # Competitor section (separate, highlighted)
        if comp_items:
            elements.append({
                "tag": "div",
                "text": {"tag": "lark_md", "content": "**⚠️ 竞品动向 · Competitor Watch**"},
            })
            for a in comp_items[:5]:
                elements.append(_format_item_card_block(a, with_competitor_intel=True))
            elements.append({"tag": "hr"})

        # Footer note about Bitable
        elements.append({
            "tag": "note",
            "elements": [{
                "tag": "plain_text",
                "content": "完整内容请查看多维表格知识库",
            }],
        })

        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "purple",
                "title": {
                    "tag": "plain_text",
                    "content": f"DEI 研究助手 · {date_label}",
                },
            },
            "elements": elements,
        }

    def send_text(self, text: str) -> bool:
        chat_id = SETTINGS.feishu_chat_id
        if not chat_id:
            return False
        self._post(
            "/im/v1/messages?receive_id_type=chat_id",
            {
                "receive_id": chat_id,
                "msg_type": "text",
                "content": json.dumps({"text": text}, ensure_ascii=False),
            },
        )
        return True

    # ---------- Docs (weekly/monthly trend report) ----------

    def create_doc_with_markdown(self, title: str, markdown: str) -> Optional[str]:
        """Create a Feishu Doc (docx) with the given markdown body.

        Returns the doc URL, or None if folder token is not configured / API fails.
        Uses the docx-import endpoint which converts markdown → docx blocks.
        """
        folder = SETTINGS.feishu_doc_folder_token
        if not folder:
            LOGGER.info("FEISHU_DOC_FOLDER_TOKEN not set — skipping doc creation")
            return None

        # Step 1: create empty docx
        try:
            data = self._post(
                "/docx/v1/documents",
                {"folder_token": folder, "title": title},
            )
            doc_id = data["data"]["document"]["document_id"]
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Failed to create docx: %s", exc)
            return None

        # Step 2: append markdown content as a single raw text block
        # Note: For full markdown→blocks fidelity, use the import API. This is a
        # simpler fallback that puts the markdown into a single text block.
        try:
            self._post(
                f"/docx/v1/documents/{doc_id}/blocks/{doc_id}/children",
                {
                    "children": [
                        {
                            "block_type": 2,  # text block
                            "text": {
                                "elements": [
                                    {
                                        "text_run": {
                                            "content": markdown,
                                            "text_element_style": {},
                                        }
                                    }
                                ],
                                "style": {},
                            },
                        }
                    ],
                    "index": 0,
                },
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Failed to append blocks to docx: %s", exc)

        url = f"https://feishu.cn/docx/{doc_id}"
        return url


def _chunks(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i : i + n]


def _group_daily_card_sections(
    items: List[AnalyzedItem],
) -> tuple[dict[str, List[AnalyzedItem]], List[AnalyzedItem]]:
    """Assign every item to exactly one daily-card section.

    `pillars` is intentionally multi-select for storage and weekly synthesis,
    but the chat digest is a short push notification. Rendering every selected
    pillar makes the same article appear repeatedly. This grouping keeps the
    digest deduplicated while preserving the full multi-pillar metadata in the
    item line itself.
    """
    sections: dict[str, List[AnalyzedItem]] = {
        "global": [],
        "mnc_china": [],
        "china_going_global": [],
    }
    competitors: List[AnalyzedItem] = []
    for item in _dedupe_analyzed_items(items):
        if item.raw.is_competitor:
            competitors.append(item)
            continue
        sections.setdefault(_primary_daily_pillar(item), []).append(item)
    return sections, competitors


def _dedupe_analyzed_items(items: List[AnalyzedItem]) -> List[AnalyzedItem]:
    """Preserve order while removing defensive duplicates by URL/hash/title."""
    seen: set[str] = set()
    out: List[AnalyzedItem] = []
    for item in items:
        key = item.raw.url or item.raw.hash or item.raw.title.strip().lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _primary_daily_pillar(item: AnalyzedItem) -> str:
    """Choose the single most useful daily-card pillar for a regular item."""
    valid = {"global", "mnc_china", "china_going_global"}
    pillars = [p for p in item.pillars if p in valid]
    if not pillars:
        return "global"
    if len(pillars) == 1:
        return pillars[0]

    segment_scores = {
        "mnc_china": item.relevance_mnc_china,
        "china_going_global": item.relevance_going_global,
    }
    scored = [
        (pillar, segment_scores[pillar])
        for pillar in pillars
        if pillar in segment_scores and segment_scores[pillar] > 0
    ]
    if scored:
        return max(scored, key=lambda x: x[1])[0]
    return "global"


def _format_item_card_block(a: AnalyzedItem, with_competitor_intel: bool = False) -> dict:
    """Render one analyzed item as a Feishu card 'div' block."""
    r = a.raw
    title = a.title_zh or r.title
    competitor_badge = " 🎯竞品" if r.is_competitor else ""
    topics = " · ".join(a.topics[:3]) if a.topics else ""
    # Three client-relevance scores compactly displayed (skip zeros)
    rel_parts = []
    for label, score in [
        ("跨国在华", a.relevance_mnc_china),
        ("ESG/上市", a.relevance_esg_listing),
        ("出海",       a.relevance_going_global),
    ]:
        if score > 0:
            rel_parts.append(f"{label} {score}/5")
    rel_line = " · ".join(rel_parts) or "—"

    pieces = [
        f"**[{title}]({r.url})**{competitor_badge}",
        f"📍 {r.source_name}  |  ⭐ {rel_line}" + (f"  |  🏷 {topics}" if topics else ""),
        f"{a.zh_summary}",
    ]
    # Highlight the strongest client-segment implication
    if a.relevance_mnc_china >= max(a.relevance_esg_listing, a.relevance_going_global) and a.implication_mnc_china and "不直接相关" not in a.implication_mnc_china:
        pieces.append(f"💡 *对在华跨国客户：* {a.implication_mnc_china}")
    elif a.relevance_esg_listing >= a.relevance_going_global and a.implication_esg_listing and "不直接相关" not in a.implication_esg_listing:
        pieces.append(f"💡 *对ESG/上市客户：* {a.implication_esg_listing}")
    elif a.implication_going_global and "不直接相关" not in a.implication_going_global:
        pieces.append(f"💡 *对出海客户：* {a.implication_going_global}")

    if with_competitor_intel and a.competitor_intelligence:
        pieces.append(f"🎯 *竞品情报：* {a.competitor_intelligence}")

    return {"tag": "div", "text": {"tag": "lark_md", "content": "\n\n".join(pieces)}}
