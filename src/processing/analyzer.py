"""LLM-powered analysis: per-item enrichment + weekly trend synthesis.

Default backend: OpenAI Responses API (`openai` package).
Anthropic is still available by setting LLM_PROVIDER=anthropic.

Setup:
- Set OPENAI_API_KEY in .env (local) or GitHub Secrets (CI).
- Optionally override OPENAI_MODEL (default: gpt-5.4-mini).
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from anthropic import (
    Anthropic,
    APIStatusError as AnthropicAPIStatusError,
    APITimeoutError as AnthropicAPITimeoutError,
    RateLimitError as AnthropicRateLimitError,
)
from openai import (
    OpenAI,
    APIStatusError as OpenAIAPIStatusError,
    APITimeoutError as OpenAIAPITimeoutError,
    RateLimitError as OpenAIRateLimitError,
)
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

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

RETRYABLE_LLM_ERRORS = (
    AnthropicRateLimitError,
    AnthropicAPITimeoutError,
    AnthropicAPIStatusError,
    OpenAIRateLimitError,
    OpenAIAPITimeoutError,
    OpenAIAPIStatusError,
)


@dataclass
class AnalyzedItem:
    """v2 analyzed item.

    Field naming convention:
    - implication_*  : per-client-segment narrative ("不直接相关" if not applicable)
    - relevance_*    : per-client-segment 0-5 score
    - overall_relevance : single composite score (replaces v1's relevance_score)

    `china_implication` is kept as a backward-compatibility alias for
    `implication_mnc_china`. New code should write to implication_mnc_china;
    legacy callers reading china_implication continue to work via property.
    """

    raw: RawItem

    # Bilingual outputs
    title_zh: str = ""
    en_summary: str = ""
    zh_summary: str = ""
    key_takeaways: List[str] = field(default_factory=list)

    # Three pillars (multi-select): subset of {"global", "mnc_china", "china_going_global"}
    pillars: List[str] = field(default_factory=list)

    # Per-client-segment outputs
    implication_mnc_china: str = ""
    implication_esg_listing: str = ""
    implication_going_global: str = ""
    relevance_mnc_china: int = 0
    relevance_esg_listing: int = 0
    relevance_going_global: int = 0

    # Competitor intelligence (only set if raw.is_competitor=True)
    competitor_intelligence: str = ""

    # v2.1: stance — narrative dimension for "DEI 回撤 vs 坚守" framing.
    # One of: "" (neutral) / backlash / persist / mainstream / controversy
    stance: str = ""

    # Tags & quality
    topics: List[str] = field(default_factory=list)
    industries: List[str] = field(default_factory=list)
    evidence_type: str = ""
    rigor_score: int = 0
    overall_relevance: int = 0

    # Metadata
    analyzed_at: Optional[datetime] = None
    error: Optional[str] = None

    # ---- Backward-compatibility aliases (v1 callers) ----

    @property
    def china_implication(self) -> str:
        """v1 alias — maps to implication_mnc_china."""
        return self.implication_mnc_china

    @china_implication.setter
    def china_implication(self, value: str) -> None:
        self.implication_mnc_china = value

    @property
    def relevance_score(self) -> int:
        """v1 alias — maps to overall_relevance."""
        return self.overall_relevance

    @relevance_score.setter
    def relevance_score(self, value: int) -> None:
        self.overall_relevance = value

    def is_valid(self) -> bool:
        return bool(self.zh_summary and not self.error)


class Analyzer:
    def __init__(self, model: Optional[str] = None) -> None:
        self.provider = SETTINGS.llm_provider
        if self.provider == "openai":
            self.model = model or SETTINGS.openai_model
            api_key = SETTINGS.openai_api_key
            if not api_key or api_key.startswith("sk-xxxxxxxx") or api_key.startswith("sk-proj-xxxxxxxx"):
                raise RuntimeError(
                    "OPENAI_API_KEY is missing or still set to the placeholder. "
                    "Set a real key in .env (local) or GitHub Secrets (CI)."
                )
            self.client = OpenAI(api_key=api_key, max_retries=0)
        elif self.provider == "anthropic":
            self.model = model or SETTINGS.claude_model
            api_key = SETTINGS.anthropic_api_key
            if not api_key or api_key.startswith("sk-ant-xxx"):
                raise RuntimeError(
                    "ANTHROPIC_API_KEY is missing or still set to the placeholder. "
                    "Set a real key in .env (local) or GitHub Secrets (CI)."
                )
            self.client = Anthropic(api_key=api_key)
        else:
            raise RuntimeError(
                "Unsupported LLM_PROVIDER. Use 'openai' or 'anthropic'."
            )
        self.request_delay_seconds = SETTINGS.llm_request_delay_seconds

    # ------------- per-item -------------

    @retry(
        stop=stop_after_attempt(4),
        wait=wait_exponential(min=20, max=60),
        retry=retry_if_exception_type(RETRYABLE_LLM_ERRORS),
        reraise=True,
    )
    def _call(self, system: str, user: str, max_tokens: int = 2500) -> str:
        if self.provider == "openai":
            return self._call_openai(system, user, max_tokens=max_tokens)
        return self._call_anthropic(system, user, max_tokens=max_tokens)

    def _call_openai(self, system: str, user: str, max_tokens: int = 2500) -> str:
        response = self.client.responses.create(
            model=self.model,
            instructions=system,
            input=user,
            max_output_tokens=max_tokens,
        )
        text = getattr(response, "output_text", None)
        if text:
            return text.strip()

        chunks: list[str] = []
        for item in getattr(response, "output", []) or []:
            for block in getattr(item, "content", []) or []:
                if getattr(block, "type", "") == "output_text":
                    chunks.append(getattr(block, "text", ""))
        return "".join(chunks).strip()

    def _call_anthropic(self, system: str, user: str, max_tokens: int = 2500) -> str:
        # Cache the system prompt (ephemeral, 5-min TTL) so back-to-back
        # items in a batch reuse the cached prefix at ~10% the input cost.
        msg = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=[
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user}],
        )
        return "".join(
            block.text for block in msg.content if getattr(block, "type", "") == "text"
        ).strip()

    def analyze_item(self, item: RawItem) -> AnalyzedItem:
        from ..utils import now_utc

        content = clean_text(item.summary, max_chars=5000) or item.title
        prompt = ANALYZE_USER_TEMPLATE.format(
            title=item.title,
            source_name=item.source_name,
            source_category=item.source_category,
            region=item.region,
            is_competitor_zh="是" if item.is_competitor else "否",
            authors=", ".join(item.authors) or "未署名",
            published_at=item.published_at.isoformat() if item.published_at else "未知",
            url=item.url,
            content=content,
        )
        analyzed = AnalyzedItem(raw=item, analyzed_at=now_utc())
        try:
            raw = self._call(ANALYZE_SYSTEM, prompt, max_tokens=2500)
            data = _extract_json(raw)
            analyzed.title_zh = data.get("title_zh", "") or ""
            analyzed.en_summary = data.get("en_summary", "") or ""
            analyzed.zh_summary = data.get("zh_summary", "") or ""
            analyzed.key_takeaways = data.get("key_takeaways", []) or []
            analyzed.pillars = _normalize_pillars(data.get("pillars", []))
            analyzed.implication_mnc_china = data.get("implication_mnc_china", "") or ""
            analyzed.implication_esg_listing = data.get("implication_esg_listing", "") or ""
            analyzed.implication_going_global = data.get("implication_going_global", "") or ""
            analyzed.relevance_mnc_china = _clamp_score(data.get("relevance_mnc_china"))
            analyzed.relevance_esg_listing = _clamp_score(data.get("relevance_esg_listing"))
            analyzed.relevance_going_global = _clamp_score(data.get("relevance_going_global"))
            if item.is_competitor:
                analyzed.competitor_intelligence = data.get("competitor_intelligence", "") or ""
            else:
                analyzed.competitor_intelligence = ""
            analyzed.topics = data.get("topics", []) or []
            analyzed.industries = data.get("industries", []) or []
            analyzed.evidence_type = data.get("evidence_type", "") or ""
            analyzed.rigor_score = _clamp_score(data.get("rigor_score"), max_v=5)
            analyzed.overall_relevance = _clamp_score(
                data.get("overall_relevance", data.get("relevance_score")), max_v=5
            )
            stance = (data.get("stance") or "").strip().lower()
            analyzed.stance = stance if stance in {"backlash", "persist", "mainstream", "controversy"} else ""
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Analyze failed for %s: %s", item.url, exc)
            analyzed.error = str(exc)
        return analyzed

    def analyze_batch(self, items: List[RawItem]) -> List[AnalyzedItem]:
        results: List[AnalyzedItem] = []
        for i, item in enumerate(items, 1):
            if i > 1 and self.request_delay_seconds > 0:
                LOGGER.info(
                    "Waiting %.1fs before next LLM request to respect rate limits",
                    self.request_delay_seconds,
                )
                time.sleep(self.request_delay_seconds)
            LOGGER.info("[%d/%d] Analyzing: %s", i, len(items), item.title[:80])
            results.append(self.analyze_item(item))
        return results

    # ------------- weekly synthesis -------------

    def weekly_trend_report(
        self, items: List[AnalyzedItem], start_date: str, end_date: str
    ) -> str:
        compact = [
            {
                "title": a.title_zh or a.raw.title,
                "url": a.raw.url,
                "source": a.raw.source_name,
                "source_category": a.raw.source_category,
                "region": a.raw.region,
                "is_competitor": a.raw.is_competitor,
                "pillars": a.pillars,
                "zh_summary": a.zh_summary,
                "topics": a.topics,
                "rigor": a.rigor_score,
                "overall_relevance": a.overall_relevance,
                "relevance_mnc_china": a.relevance_mnc_china,
                "relevance_esg_listing": a.relevance_esg_listing,
                "relevance_going_global": a.relevance_going_global,
                "competitor_intelligence": a.competitor_intelligence,
                "stance": a.stance,
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


_VALID_PILLARS = {"global", "mnc_china", "china_going_global"}


def _normalize_pillars(value) -> List[str]:
    """Coerce model output for `pillars` into a clean list[str]."""
    if not value:
        return []
    if isinstance(value, str):
        value = [v.strip() for v in value.replace(";", ",").split(",")]
    if not isinstance(value, list):
        return []
    seen = set()
    out: List[str] = []
    for v in value:
        if not isinstance(v, str):
            continue
        v = v.strip().lower()
        v = {
            "mnc_in_china": "mnc_china",
            "going_global": "china_going_global",
            "global_frontier": "global",
        }.get(v, v)
        if v in _VALID_PILLARS and v not in seen:
            out.append(v)
            seen.add(v)
    return out


def _clamp_score(value, min_v: int = 0, max_v: int = 5) -> int:
    """Coerce a model-supplied score into an int in [min_v, max_v]."""
    if value is None:
        return min_v
    try:
        n = int(value)
    except (TypeError, ValueError):
        try:
            n = int(float(value))
        except (TypeError, ValueError):
            return min_v
    return max(min_v, min(max_v, n))


def _extract_json(text: str) -> dict:
    """Tolerant JSON extraction (handles ```json fences, ASCII-quote breakage, etc.)."""
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
        raise
