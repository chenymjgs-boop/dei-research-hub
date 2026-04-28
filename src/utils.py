"""Shared helpers: logging, retry, hashing, date parsing."""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timezone
from typing import Optional

from dateutil import parser as date_parser

LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def url_hash(url: str) -> str:
    """Stable short hash for deduplication."""
    return hashlib.sha1(url.strip().lower().encode("utf-8")).hexdigest()[:16]


def parse_date(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = date_parser.parse(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return None


def clean_text(text: Optional[str], max_chars: int = 6000) -> str:
    """Strip HTML/whitespace and truncate. Used to bound LLM input size."""
    if not text:
        return ""
    # Strip HTML tags
    text = re.sub(r"<[^>]+>", " ", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_chars:
        text = text[:max_chars] + "…"
    return text


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
