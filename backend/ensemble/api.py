"""ボードのAPI。家族コードはURLのパスにも問い合わせ文字列にも入れない。

アクセス記録にはパスが残るので、画面のURLは `/b#コード`（# より後ろはサーバーへ届かない）にし、
APIにはヘッダで渡す。どのURLも同じなので、応答は保存させない。
"""

import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, FastAPI, Header, HTTPException, Request, Response
from pydantic import Field, ValidationError
from sqlalchemy.engine import Engine

from .budget import JST
from .guards import (
    INPUT_MAX_CHARS,
    THIRD_PARTY_NOTICE,
    check_input_length,
    is_third_party,
    lower_certainty,
    medical_reply,
    strip_third_party,
    unmask_aliases,
)
from .ledger import Certainty, Family, Item, Kind, Medical, Part, Status, _Strict, status_label
from .presets import load_family_from_preset
from .store import FamilyNotFound, VersionConflict, create_family, load_family, save_family

BOARD_NOT_FOUND = "ボードが見つかりません"
VERSION_CONFLICT = "ほかの端末で変わりました。読み直したので、もう一度押してください"
ITEM_NOT_FOUND = "項目が見つかりません"
ITEM_IN_USE = "案の根拠になっている項目は、まだ消せません"
DUE_MAX_CHARS = 50


class NewBoard(_Strict):
    home: Literal["home_a", "home_b"]


class NewItem(_Strict):
    version: int
    kind: Kind
    part: Part
    certainty: Certainty
    text_original: str | None = None
    task_summary: str | None = None
    speaker: str | None = None
    condition: str | None = None
    medical: Medical = Medical.NONE
    refers_to: str | None = None
    status: Status | None = None
    owner: str | None = None
    due: str | None = Field(default=None, max_length=DUE_MAX_CHARS)
    location: str | None = None


class StatusChange(_Strict):
    version: int
    status: Status
    due: str | None = Field(default=None, max_length=DUE_MAX_CHARS)


def board_view(family: Family) -> dict[str, Any]:
    """画面へ渡す形。家族コードは返さない。表示名と固定文はサーバーの1か所で決める。"""
    data = family.model_dump(mode="json", exclude={"code"})
    for shown, item in zip(data["items"], family.items):
        for field in ("text_original", "task_summary"):
            if shown[field]:
                shown[field] = unmask_aliases(shown[field], family.aliases)
        shown["status_label"] = status_label(item.status, item.due) if item.status else None
        shown["medical_reply"] = medical_reply(item)
        shown["notice"] = THIRD_PARTY_NOTICE if item.text_original is None else None
    return data


def require_due_for_time(item: Item) -> None:
    if item.status == Status.OK_WITH_TIME and not item.due:
        raise ValueError("時刻がありません")


def build_item(new: NewItem, now: str) -> Item:
    """入ってきた項目に取り込みの守りを当てる。APIを直接呼んでも守りを飛ばせない。"""
    if not new.text_original and not new.task_summary:
        raise ValueError("原文も用件もありません")
    if new.text_original and not check_input_length(new.text_original):
        raise ValueError(f"{INPUT_MAX_CHARS}字までです")
    item = Item(
        id=f"i-{uuid.uuid4().hex[:12]}",
        recorded_at=now,
        **new.model_dump(exclude={"version"}),
    )
    require_due_for_time(item)
    # 確かさは原文で判断するので、第三者の文を消す前に下げる。
    item = lower_certainty(item)
    return strip_third_party(item, now) if is_third_party(item) else item


def is_referenced(item_id: str, family: Family) -> bool:
    """分かれ道の条件・根拠・外す先に使われているか。"""
    for branch in family.branches:
        if branch.condition_item_id == item_id:
            return True
        for outcome in branch.outcomes:
            for action in outcome.actions:
                if item_id in action.basis_ids or item_id in action.release:
                    return True
    return False


def make_router(engine: Engine) -> APIRouter:
    router = APIRouter(prefix="/api")

    def read(code: str | None) -> Family:
        if not code:
            raise HTTPException(status_code=404, detail=BOARD_NOT_FOUND)
        try:
            return load_family(engine, code)
        except FamilyNotFound:
            raise HTTPException(status_code=404, detail=BOARD_NOT_FOUND) from None

    def require_version(family: Family, version: int) -> None:
        if family.version != version:
            raise HTTPException(status_code=409, detail=VERSION_CONFLICT)

    def save(family: Family, version: int) -> dict[str, Any]:
        """画面が見ていた版で照合する（読み直した版を使うと、衝突に気づけない）。"""
        try:
            return board_view(save_family(engine, family, version))
        except VersionConflict:
            raise HTTPException(status_code=409, detail=VERSION_CONFLICT) from None
        except FamilyNotFound:
            raise HTTPException(status_code=404, detail=BOARD_NOT_FOUND) from None

    @router.post("/boards", status_code=201)
    def make_board(body: NewBoard) -> dict[str, Any]:
        family = create_family(engine, load_family_from_preset(body.home))
        return {"code": family.code, "board": board_view(family)}

    @router.get("/board")
    def get_board(x_family_code: str | None = Header(default=None)) -> dict[str, Any]:
        return board_view(read(x_family_code))

    @router.post("/board/items", status_code=201)
    def add_item(body: NewItem, x_family_code: str | None = Header(default=None)) -> dict[str, Any]:
        family = read(x_family_code)
        require_version(family, body.version)
        try:
            item = build_item(body, datetime.now(JST).isoformat(timespec="seconds"))
        except (ValidationError, ValueError) as error:
            # 入力の中身を応答に写さない。
            raise HTTPException(status_code=422, detail="項目の形が正しくありません") from error
        family.items.append(item)
        return save(family, body.version)

    @router.patch("/board/items/{item_id}")
    def change_status(
        item_id: str, body: StatusChange, x_family_code: str | None = Header(default=None)
    ) -> dict[str, Any]:
        family = read(x_family_code)
        require_version(family, body.version)
        for index, item in enumerate(family.items):
            if item.id != item_id:
                continue
            due = body.due if body.due else item.due
            try:
                # model_copy は検査をしないので、型を通し直す。
                changed = Item.model_validate(
                    {**item.model_dump(mode="json"), "status": body.status, "due": due}
                )
                require_due_for_time(changed)
            except (ValidationError, ValueError) as error:
                raise HTTPException(status_code=422, detail="この項目には使えません") from error
            family.items[index] = changed
            return save(family, body.version)
        raise HTTPException(status_code=404, detail=ITEM_NOT_FOUND)

    @router.delete("/board/items/{item_id}")
    def remove_item(
        item_id: str, version: int, x_family_code: str | None = Header(default=None)
    ) -> dict[str, Any]:
        family = read(x_family_code)
        require_version(family, version)
        if not any(item.id == item_id for item in family.items):
            raise HTTPException(status_code=404, detail=ITEM_NOT_FOUND)
        if is_referenced(item_id, family):
            raise HTTPException(status_code=409, detail=ITEM_IN_USE)
        family.items = [item for item in family.items if item.id != item_id]
        return save(family, version)

    return router


def install_api(app: FastAPI, engine: Engine) -> None:
    @app.middleware("http")
    async def no_store_api(request: Request, call_next: Any) -> Response:
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    app.include_router(make_router(engine))
