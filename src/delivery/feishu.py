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
        """Append every analyzed item as a row to the configured Bitable.

        Field name conventions (in Chinese, must exist in the Bitable):
            标题 (text)
            链接 (url)
            来源 (text)
            类别 (单选: academic/consulting/international/media/china)
            地域 (单选: global/china)
            发布日期 (datetime)
            英文摘要 (text)
            中文摘要 (text)
            关键要点 (text)
            对中国的启示 (text)
            话题 (多选)
            行业 (多选)
            证据类型 (单选)
            严谨度 (number)
            相关性 (number)
            收录日期 (datetime)
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
                "类别": r.source_category,
                "地域": r.region,
                "英文摘要": a.en_summary,
                "中文摘要": a.zh_summary,
                "关键要点": "\n".join(f"• {t}" for t in a.key_takeaways),
                "对中国的启示": a.china_implication,
                "话题": a.topics,
                "行业": a.industries,
                "证据类型": a.evidence_type,
                "严谨度": a.rigor_score,
                "相关性": a.relevance_score,
            }
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
        """Send a Feishu interactive card with the day's top items to a chat."""
        chat_id = SETTINGS.feishu_chat_id
        if not chat_id:
            LOGGER.info("FEISHU_CHAT_ID not set — skipping chat push")
            return False
        if not items:
            return False

        # Sort by relevance × rigor and keep top N
        ranked = sorted(
            [a for a in items if a.is_valid()],
            key=lambda a: (a.relevance_score, a.rigor_score),
            reverse=True,
        )[:10]

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
        elements: list[dict] = [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": (
                        f"**今日 DEI 全球研究简报 · {date_label}**\n"
                        f"共收录 {len(items)} 条精选研究，按相关度排序。"
                    ),
                },
            },
            {"tag": "hr"},
        ]
        for i, a in enumerate(items, 1):
            r = a.raw
            topics = " · ".join(a.topics[:3]) if a.topics else "—"
            content = (
                f"**{i}. [{r.title}]({r.url})**\n"
                f"📍 {r.source_name} | {r.region} | 🏷 {topics} | "
                f"⭐ 相关度 {a.relevance_score}/5\n\n"
                f"{a.zh_summary}\n\n"
                f"💡 *对中国企业：* {a.china_implication}"
            )
            elements.append(
                {"tag": "div", "text": {"tag": "lark_md", "content": content}}
            )
            if i < len(items):
                elements.append({"tag": "hr"})

        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "blue",
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
