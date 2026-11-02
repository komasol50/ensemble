"""Claudeの呼び出し回数の上限。DBに持つので、サーバーを再起動しても数え直さない。"""

import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import insert, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from .db import ai_call_counts

JST = timezone(timedelta(hours=9))
GLOBAL_SCOPE = "global"
DEFAULT_DAILY_LIMIT = 200


class DailyLimitReached(Exception):
    """今日の上限に届いた。"""


def daily_limit() -> int:
    return int(os.environ.get("AI_DAILY_LIMIT", DEFAULT_DAILY_LIMIT))


def today_jst() -> str:
    return datetime.now(JST).strftime("%Y-%m-%d")


def consume_call(engine: Engine, *, scope: str = GLOBAL_SCOPE, limit: int | None = None) -> int:
    """1回分を数える。上限を超えるなら数えずに例外を出す。今日の回数を返す。"""
    limit = daily_limit() if limit is None else limit
    day = today_jst()
    if limit <= 0:
        raise DailyLimitReached()
    with engine.begin() as conn:
        result = conn.execute(
            update(ai_call_counts)
            .where(
                ai_call_counts.c.day == day,
                ai_call_counts.c.scope == scope,
                ai_call_counts.c.count < limit,
            )
            .values(count=ai_call_counts.c.count + 1)
            .returning(ai_call_counts.c.count)
        ).first()
        if result is not None:
            return result[0]
    try:
        with engine.begin() as conn:
            conn.execute(insert(ai_call_counts).values(day=day, scope=scope, count=1))
        return 1
    except IntegrityError:
        # 行はすでにある＝今日の分が上限に届いている（または同時に作られた）。
        raise DailyLimitReached() from None
