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


# 項目に起きたことと時刻（送った、返事、済、直した）。台帳に欄を足さないよう、別の表に置く。
# 本文は持たない。ボードやゴミ箱から完全に消すときは、一緒に消す。
# 状態を変えた記録は、変わる前の状態と期日も持つ（元に戻す先を、ここから決める）。
item_events = Table(
    "item_events",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("code", String(64), nullable=False, index=True),
    Column("item_id", String(100), nullable=False),
    Column("at", String(40), nullable=False),
    Column("label", String(120), nullable=False),
    Column("before_status", String(40), nullable=True),
    Column("before_due", String(100), nullable=True),
)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def make_engine(url: str | None = None) -> Engine:
    return create_engine(url or database_url(), pool_pre_ping=True)


def init_db(engine: Engine) -> None:
    metadata.create_all(engine)
