"""台帳の保存。版番号が合わない更新は受け付けない（2台で同時に触っても消えないように）。"""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import delete, insert, select, update
from sqlalchemy.engine import Engine

from .db import families, item_events, now_utc, planners, trash
from .ledger import Family


class VersionConflict(Exception):
    """手元の版が古い。読み直してからやり直す。"""


class FamilyNotFound(Exception):
    """その家族コードの台帳が無い。"""


@dataclass(frozen=True)
class ItemEvent:
    """項目に起きたこと1つ。本文は持たない。"""

    item_id: str
    at: str
    label: str
    before_status: str | None = None
    before_due: str | None = None


def stored(family: Family) -> dict:
    """台帳を保存する形。連絡する相手（①）と入院した日が未設定なら欄を書かない。

    公開中の前の版は知らない欄を読めないので、試す家庭や設定していない家庭の台帳を、前の版へ戻しても
    開けるようにする（QA Test の指摘。戻しの非互換）。
    """
    data = family.model_dump(mode="json")
    if data.get("profile", {}).get("contacts") is None:
        data.get("profile", {}).pop("contacts", None)
    # 入院した日も同じ。入れていない台帳には欄を書かない。
    if data.get("admitted_on") is None:
        data.pop("admitted_on", None)
    return data


def create_family(engine: Engine, family: Family) -> Family:
    with engine.begin() as conn:
        conn.execute(
            insert(families).values(
                code=family.code,
                version=family.version,
                ledger=stored(family),
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


def save_family(
    engine: Engine,
    family: Family,
    expected_version: int,
    events: Sequence[ItemEvent] = (),
) -> Family:
    """expected_version が今の版と同じときだけ保存し、版を1つ進める。

    WHERE に版番号を入れた1回のUPDATEで照合するので、読んでから書くまでの間に
    割り込まれても、後から来た方が失敗する。起きたことの記録は同じ取引で書くので、
    台帳と食い違わない。
    """
    next_version = expected_version + 1
    saved = family.model_copy(update={"version": next_version})
    with engine.begin() as conn:
        result = conn.execute(
            update(families)
            .where(families.c.code == family.code, families.c.version == expected_version)
            .values(
                version=next_version,
                ledger=stored(saved),
                updated_at=now_utc(),
            )
        )
        if result.rowcount == 1:
            for event in events:
                conn.execute(
                    insert(item_events).values(
                        code=family.code,
                        item_id=event.item_id,
                        at=event.at,
                        label=event.label,
                        before_status=event.before_status,
                        before_due=event.before_due,
                    )
                )
            return saved
        exists = conn.execute(select(families.c.code).where(families.c.code == family.code)).first()
    if exists is None:
        raise FamilyNotFound()
    raise VersionConflict()


def list_events(engine: Engine, code: str) -> dict[str, list[dict[str, str]]]:
    """項目ごとの、起きたことの一覧。起きた順。"""
    with engine.connect() as conn:
        rows = conn.execute(
            select(item_events.c.item_id, item_events.c.at, item_events.c.label)
            .where(item_events.c.code == code)
            .order_by(item_events.c.id)
        ).all()
    events: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        events[row.item_id].append({"at": row.at, "label": row.label})
    return dict(events)


def list_item_events(engine: Engine, code: str, item_id: str) -> list[ItemEvent]:
    """1つの項目の記録を、変わる前の状態も含めて読む。画面へは出さない。"""
    with engine.connect() as conn:
        rows = conn.execute(
            select(item_events)
            .where(item_events.c.code == code, item_events.c.item_id == item_id)
            .order_by(item_events.c.id)
        ).all()
    return [
        ItemEvent(row.item_id, row.at, row.label, row.before_status, row.before_due) for row in rows
    ]


def forget_events(engine: Engine, code: str, keep: set[str]) -> None:
    """台帳にもゴミ箱にも無くなった項目の記録を消す。"""
    with engine.begin() as conn:
        rows = conn.execute(
            select(item_events.c.item_id).where(item_events.c.code == code).distinct()
        ).all()
        gone = [row.item_id for row in rows if row.item_id not in keep]
        if gone:
            conn.execute(
                delete(item_events).where(
                    item_events.c.code == code, item_events.c.item_id.in_(gone)
                )
            )


def delete_family(engine: Engine, code: str) -> None:
    """台帳とゴミ箱と起きたことの記録を、行ごと消す。元に戻せない。"""
    with engine.begin() as conn:
        conn.execute(delete(trash).where(trash.c.code == code))
        conn.execute(delete(item_events).where(item_events.c.code == code))
        conn.execute(delete(planners).where(planners.c.code == code))
        result = conn.execute(delete(families).where(families.c.code == code))
    if result.rowcount == 0:
        raise FamilyNotFound()


def load_planner(engine: Engine, code: str) -> dict:
    """持ち物と知らせ。無ければ空。"""
    with engine.connect() as conn:
        row = conn.execute(select(planners).where(planners.c.code == code)).first()
    return dict(row.data) if row is not None else {}


def save_planner(engine: Engine, code: str, data: dict) -> None:
    """持ち物と知らせを丸ごと置き換える（後から来た方が残る。親が自分の端末で触るもの）。"""
    with engine.begin() as conn:
        found = conn.execute(select(planners.c.code).where(planners.c.code == code)).first()
        if found is None:
            conn.execute(insert(planners).values(code=code, data=data, updated_at=now_utc()))
        else:
            conn.execute(
                update(planners)
                .where(planners.c.code == code)
                .values(data=data, updated_at=now_utc())
            )


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


def replace_trash(engine: Engine, code: str, trash_id: str, entry: dict) -> None:
    """ゴミ箱の控えを書き換える（あだ名を変えたときの伏せ字の付け替えだけに使う）。"""
    with engine.begin() as conn:
        conn.execute(
            update(trash).where(trash.c.code == code, trash.c.id == trash_id).values(entry=entry)
        )


def remove_trash(engine: Engine, code: str, trash_id: str) -> bool:
    with engine.begin() as conn:
        result = conn.execute(delete(trash).where(trash.c.code == code, trash.c.id == trash_id))
    return result.rowcount == 1
