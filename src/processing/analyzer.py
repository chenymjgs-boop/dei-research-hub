"""Claude-powered analysis: per-item enrichment + weekly trend synthesis.

Backend: Claude Agent SDK (uses the Claude Code CLI's local authentication —
i.e. your Claude Max subscription). No ANTHROPIC_API_KEY required.

Requirements:
- Claude Code CLI installed and logged in (`claude /login`)
- `claude-agent-sdk` Python package
"""
from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    TextBlock,
    query,
)
from tenacity import retry, stop_after_attempt, wait_exponential

from ..config import SETTINGS
from ..sources.base import RawItem
from ..utils import clean_text, get_logger
from .prompts import (
    ANALYZE_SYSTEM,
    ANALYZE_USER_TEMPLATE,
    WEEKLY_TREND_SYSTEM,
    WEEKLY_TREND_USER,
)

LOGGER = get_logger(__name__)


@dataclass
class AnalyzedItem:
    raw: RawItem
    en_summary: str = ""
    zh_summary: str = ""
    key_takeaways: List[str] = field(default_factory=list)
    china_implication: str = ""
    topics: List[str] = field(default_factory=list)
    industries: List[str] = field(default_factory=list)
    evidence_type: str = ""
    rigor_score: int = 0
    relevance_score: int = 0
    analyzed_at: Optional[datetime] = None
    error: Optional[str] = None

    def is_valid(self) -> bool:
        return bool(self.zh_summary and not self.error)


class Analyzer:
    def __init__(self, model: Optional[str] = None) -> None:
        # `model` is forwarded to the Claude Code CLI as --model; if the CLI
        # rejects an unknown value it falls back to the default for the
        # subscription tier. No API key required.
        self.model = model or SETTINGS.claude_model

    # ------------- per-item -------------

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=20), reraise=True)
    def _call(self, system: str, user: str, max_tokens: int = 1500) -> str:
        # max_tokens is unused — the SDK / CLI controls token budget.
        # Kept in signature for backward compatibility with callers.
        return asyncio.run(self._call_async(system, user))

    async def _call_async(self, system: str, user: str) -> str:
        # Strip ANTHROPIC_API_KEY from the subprocess env. Otherwise the bundled
        # CLI prefers it over OAuth and any placeholder/invalid key in .env will
        # cause "Invalid API key" failures. Removing it forces the CLI to use
        # the Max-subscription OAuth credentials it was logged into.
        sub_env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}

        options = ClaudeAgentOptions(
            system_prompt=system,
            # When the prompt contains a URL, the model often tries to call
            # WebFetch first. With allowed_tools=[] each attempt is denied and
            # consumes a turn. The system prompt now explicitly forbids tool
            # use, but a stubborn model may still attempt one or two times
            # before complying. Eight turns gives ample headroom while still
            # bounding total cost / latency per item.
            max_turns=8,
            allowed_tools=[],
            model=self.model,
            env=sub_env,
        )
        parts: List[str] = []
        async for msg in query(prompt=user, options=options):
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        parts.append(block.text)
        return "\n".join(parts).strip()

    def analyze_item(self, item: RawItem) -> AnalyzedItem:
        from ..utils import now_utc

        content = clean_text(item.summary, max_chars=5000) or item.title
        # Google News redirect URLs cannot be fetched (the page is JS-only and
        # there's no HTTP redirect). Annotate so the model doesn't waste turns
        # attempting WebFetch — even though the system prompt forbids tools,
        # an explicit hint at the URL itself further reduces stubborn retries.
        url_for_prompt = item.url
        if "news.google.com" in item.url:
            url_for_prompt = (
                f"{item.url} (Google News redirect — not directly fetchable; "
                "analyze using only the title and summary below)"
            )
        prompt = ANALYZE_USER_TEMPLATE.format(
            title=item.title,
            source_name=item.source_name,
            source_category=item.source_category,
            region=item.region,
            authors=", ".join(item.authors) or "未署名",
            published_at=item.published_at.isoformat() if item.published_at else "未知",
            url=url_for_prompt,
            content=content,
        )
        analyzed = AnalyzedItem(raw=item, analyzed_at=now_utc())
        try:
            raw = self._call(ANALYZE_SYSTEM, prompt, max_tokens=1500)
            data = _extract_json(raw)
            analyzed.en_summary = data.get("en_summary", "")
            analyzed.zh_summary = data.get("zh_summary", "")
            analyzed.key_takeaways = data.get("key_takeaways", []) or []
            analyzed.china_implication = data.get("china_implication", "")
            analyzed.topics = data.get("topics", []) or []
            analyzed.industries = data.get("industries", []) or []
            analyzed.evidence_type = data.get("evidence_type", "")
            analyzed.rigor_score = int(data.get("rigor_score", 0) or 0)
            analyzed.relevance_score = int(data.get("relevance_score", 0) or 0)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Analyze failed for %s: %s", item.url, exc)
            analyzed.error = str(exc)
        return analyzed

    def analyze_batch(self, items: List[RawItem]) -> List[AnalyzedItem]:
        results: List[AnalyzedItem] = []
        for i, item in enumerate(items, 1):
            LOGGER.info("[%d/%d] Analyzing: %s", i, len(items), item.title[:80])
            results.append(self.analyze_item(item))
        return results

    # ------------- weekly synthesis -------------

    def weekly_trend_report(
        self, items: List[AnalyzedItem], start_date: str, end_date: str
    ) -> str:
        compact = [
            {
                "title": a.raw.title,
                "url": a.raw.url,
                "source": a.raw.source_name,
                "category": a.raw.source_category,
                "region": a.raw.region,
                "zh_summary": a.zh_summary,
                "topics": a.topics,
                "rigor": a.rigor_score,
                "relevance": a.relevance_score,
            }
            for a in items
            if a.is_valid()
        ]
        prompt = WEEKLY_TREND_USER.format(
            days=7,
            count=len(compact),
            items_json=json.dumps(compact, ensure_ascii=False, indent=2)[:50000],
            start_date=start_date,
            end_date=end_date,
        )
        return self._call(WEEKLY_TREND_SYSTEM, prompt, max_tokens=4000)


def _extract_json(text: str) -> dict:
    """Tolerant JSON extraction.

    Strategy (in order of preference):
    1. Strip ```json fences and isolate the outermost {...} block.
    2. Try strict json.loads.
    3. On failure, fall back to `json_repair`, which handles the most common
       breakages: unescaped ASCII double-quotes inside string values
       (a frequent failure mode for Chinese-language outputs), trailing commas,
       missing braces, etc.
    """
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        from json_repair import repair_json

        repaired = repair_json(text, return_objects=True)
        if isinstance(repaired, dict):
            return repaired
        # `repair_json` may return a string when even repair fails — re-raise
        # the original error semantics in that case.
        raise
