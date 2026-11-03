"""台帳の保存。版番号が合わない更新は受け付けない（2台で同時に触っても消えないように）。"""

from sqlalchemy import delete, insert, select, update
from sqlalchemy.engine import Engine

from .db import families, now_utc, trash
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
    """台帳とゴミ箱を行ごと消す。元に戻せない。"""
    with engine.begin() as conn:
        conn.execute(delete(trash).where(trash.c.code == code))
        result = conn.execute(delete(families).where(families.c.code == code))
    if result.rowcount == 0:
        raise FamilyNotFound()


def add_trash(engine: Engine, code: str, trash_id: str, entry: dict) -> None:
    with engine.begin() as conn:
        conn.execute(
            insert(trash).values(id=trash_id, code=code, deleted_at=now_utc(), entry=entry)
        )


def list_trash(engine: Engine, code: str) -> list[tuple[str, dict]]:
    """新しく消した順。"""
    with engine.connect() as conn:
        rows = conn.execute(
            select(trash.c.id, trash.c.entry)
            .where(trash.c.code == code)
            .order_by(trash.c.deleted_at.desc())
        ).all()
    return [(row.id, row.entry) for row in rows]


def get_trash(engine: Engine, code: str, trash_id: str) -> dict | None:
    with engine.connect() as conn:
        row = conn.execute(
            select(trash.c.entry).where(trash.c.code == code, trash.c.id == trash_id)
        ).first()
    return row.entry if row else None


def remove_trash(engine: Engine, code: str, trash_id: str) -> bool:
    with engine.begin() as conn:
        result = conn.execute(delete(trash).where(trash.c.code == code, trash.c.id == trash_id))
    return result.rowcount == 1
