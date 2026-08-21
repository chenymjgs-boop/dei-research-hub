"""Centralized configuration loaded from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
# override=True so values in .env always win over an inherited shell env
# (e.g. desktop apps occasionally export API keys as "" which would otherwise
# silently shadow the real key in .env).
load_dotenv(ROOT / ".env", override=True)

DATA_DIR = ROOT / "data"
REPORTS_DIR = ROOT / "reports"
DATA_DIR.mkdir(exist_ok=True)
REPORTS_DIR.mkdir(exist_ok=True)


def _env_str(name: str, default: str = "") -> str:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def _env_int(name: str, default: int) -> int:
    value = _env_str(name)
    if value == "":
        return default
    return int(value)


def _env_float(name: str, default: float) -> float:
    value = _env_str(name)
    if value == "":
        return default
    return float(value)


@dataclass(frozen=True)
class Settings:
    # LLM provider
    llm_provider: str

    # OpenAI
    openai_api_key: str
    openai_model: str
    llm_request_delay_seconds: float

    # Claude
    anthropic_api_key: str
    claude_model: str

    # Feishu
    feishu_app_id: str
    feishu_app_secret: str
    feishu_bitable_app_token: str
    feishu_bitable_table_id: str
    feishu_chat_id: str
    feishu_doc_folder_token: str
    feishu_manual_intake_chat_id: str  # Phase 3c Layer 3 — wechat link forwarding group

    # Run params
    max_items_per_run: int
    max_items_per_source: int
    lookback_days: int

    # Paths
    db_path: Path = DATA_DIR / "research.db"
    reports_dir: Path = REPORTS_DIR


def load_settings() -> Settings:
    return Settings(
        llm_provider=_env_str("LLM_PROVIDER", "openai").lower(),
        openai_api_key=_env_str("OPENAI_API_KEY"),
        openai_model=_env_str("OPENAI_MODEL", "gpt-5.4-mini"),
        llm_request_delay_seconds=_env_float("LLM_REQUEST_DELAY_SECONDS", 25),
        anthropic_api_key=_env_str("ANTHROPIC_API_KEY"),
        claude_model=_env_str("CLAUDE_MODEL", "claude-sonnet-4-6"),
        feishu_app_id=_env_str("FEISHU_APP_ID"),
        feishu_app_secret=_env_str("FEISHU_APP_SECRET"),
        feishu_bitable_app_token=_env_str("FEISHU_BITABLE_APP_TOKEN"),
        feishu_bitable_table_id=_env_str("FEISHU_BITABLE_TABLE_ID"),
        feishu_chat_id=_env_str("FEISHU_CHAT_ID"),
        feishu_doc_folder_token=_env_str("FEISHU_DOC_FOLDER_TOKEN"),
        feishu_manual_intake_chat_id=_env_str("FEISHU_MANUAL_INTAKE_CHAT_ID"),
        max_items_per_run=_env_int("MAX_ITEMS_PER_RUN", 40),
        max_items_per_source=_env_int("MAX_ITEMS_PER_SOURCE", 8),
        lookback_days=_env_int("LOOKBACK_DAYS", 7),
    )


SETTINGS = load_settings()
