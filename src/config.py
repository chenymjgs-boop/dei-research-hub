"""Centralized configuration loaded from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
# override=True so values in .env always win over an inherited shell env
# (e.g. desktop apps occasionally export ANTHROPIC_API_KEY="" which would
# otherwise silently shadow the real key in .env).
load_dotenv(ROOT / ".env", override=True)

DATA_DIR = ROOT / "data"
REPORTS_DIR = ROOT / "reports"
DATA_DIR.mkdir(exist_ok=True)
REPORTS_DIR.mkdir(exist_ok=True)


@dataclass(frozen=True)
class Settings:
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

    # Run params
    max_items_per_run: int
    max_items_per_source: int
    lookback_days: int

    # Paths
    db_path: Path = DATA_DIR / "research.db"
    reports_dir: Path = REPORTS_DIR


def load_settings() -> Settings:
    return Settings(
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        claude_model=os.getenv("CLAUDE_MODEL", "claude-opus-4-6"),
        feishu_app_id=os.getenv("FEISHU_APP_ID", ""),
        feishu_app_secret=os.getenv("FEISHU_APP_SECRET", ""),
        feishu_bitable_app_token=os.getenv("FEISHU_BITABLE_APP_TOKEN", ""),
        feishu_bitable_table_id=os.getenv("FEISHU_BITABLE_TABLE_ID", ""),
        feishu_chat_id=os.getenv("FEISHU_CHAT_ID", ""),
        feishu_doc_folder_token=os.getenv("FEISHU_DOC_FOLDER_TOKEN", ""),
        max_items_per_run=int(os.getenv("MAX_ITEMS_PER_RUN", "40")),
        max_items_per_source=int(os.getenv("MAX_ITEMS_PER_SOURCE", "8")),
        lookback_days=int(os.getenv("LOOKBACK_DAYS", "7")),
    )


SETTINGS = load_settings()
