"""台帳の保存。版番号が合わない更新は受け付けない（2台で同時に触っても消えないように）。"""

from sqlalchemy import delete, insert, select, update
from sqlalchemy.engine import Engine

from .db import families, now_utc
from .ledger import Family


class VersionConflict(Exception):
    """手元の版が古い。読み直してからやり直す。"""


class FamilyNotFound(Exception):
    """その家族コードの台帳が無い。"""


def create_family(engine: Engine, family: Family) -> Family:
    with engine.begin() as conn:
        conn.execute(
            insert(families).values(
                code=family.code,
                version=family.version,
                ledger=family.model_dump(mode="json"),
                updated_at=now_utc(),
            )
        )
    return family


def load_family(engine: Engine, code: str) -> Family:
    with engine.connect() as conn:
        row = conn.execute(select(families).where(families.c.code == code)).first()
    if row is None:
        raise FamilyNotFound()
    data = dict(row.ledger)
    data["version"] = row.version
    return Family.model_validate(data)


def save_family(engine: Engine, family: Family, expected_version: int) -> Family:
    """expected_version が今の版と同じときだけ保存し、版を1つ進める。

    WHERE に版番号を入れた1回のUPDATEで照合するので、読んでから書くまでの間に
    割り込まれても、後から来た方が失敗する。
    """
    next_version = expected_version + 1
    saved = family.model_copy(update={"version": next_version})
    with engine.begin() as conn:
        result = conn.execute(
            update(families)
            .where(families.c.code == family.code, families.c.version == expected_version)
            .values(
                version=next_version,
                ledger=saved.model_dump(mode="json"),
                updated_at=now_utc(),
            )
        )
        if result.rowcount == 1:
            return saved
        exists = conn.execute(select(families.c.code).where(families.c.code == family.code)).first()
    if exists is None:
        raise FamilyNotFound()
    raise VersionConflict()


def delete_family(engine: Engine, code: str) -> None:
    """台帳を行ごと消す。元に戻せない。"""
    with engine.begin() as conn:
        result = conn.execute(delete(families).where(families.c.code == code))
    if result.rowcount == 0:
        raise FamilyNotFound()
