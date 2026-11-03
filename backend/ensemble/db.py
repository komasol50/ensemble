"""DBにつなぐ層。SQLite と Postgres で同じように動く書き方だけを使う。

台帳は1家族1行のJSONとして持つ。SQLの方言に頼る検索はしない。
"""

from datetime import datetime, timezone

from sqlalchemy import JSON, Column, DateTime, Integer, MetaData, String, Table, create_engine
from sqlalchemy.engine import Engine

from .config import database_url

metadata = MetaData()

families = Table(
    "families",
    metadata,
    Column("code", String(64), primary_key=True),
    Column("version", Integer, nullable=False, default=1),
    Column("ledger", JSON, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)


ai_call_counts = Table(
    "ai_call_counts",
    metadata,
    Column("day", String(10), primary_key=True),
    Column("scope", String(64), primary_key=True),
    Column("count", Integer, nullable=False),
)


# 消した項目と、戻すための控え。台帳に欄を足さないよう、別の表に置く。
trash = Table(
    "trash",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("code", String(64), nullable=False, index=True),
    Column("deleted_at", DateTime(timezone=True), nullable=False),
    Column("entry", JSON, nullable=False),
)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def make_engine(url: str | None = None) -> Engine:
    return create_engine(url or database_url(), pool_pre_ping=True)


def init_db(engine: Engine) -> None:
    metadata.create_all(engine)
