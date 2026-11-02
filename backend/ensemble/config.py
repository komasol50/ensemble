"""設定。.env を環境変数より優先する（別用途のキーを誤って使わないため）。"""

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env", override=True)

DEFAULT_SQLITE_URL = f"sqlite:///{(ROOT / 'ensemble.db').as_posix()}"
FRONTEND_DIST = ROOT / "frontend" / "dist"
AI_LOG_PATH = ROOT / "logs" / "ai_calls.jsonl"


def normalize_database_url(url: str | None) -> str:
    """接続文字列を SQLAlchemy の形にそろえる。配信基盤によって postgres:// で渡される。"""
    if not url:
        return DEFAULT_SQLITE_URL
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    return url


def database_url() -> str:
    return normalize_database_url(os.environ.get("DATABASE_URL"))
