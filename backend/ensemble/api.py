"""ボードのAPI。家族コードはURLのパスにも問い合わせ文字列にも入れない。

アクセス記録にはパスが残るので、画面のURLは `/b#コード`（# より後ろはサーバーへ届かない）にし、
APIにはヘッダで渡す。どのURLも同じなので、応答は保存させない。
"""

import uuid
from collections import OrderedDict
from datetime import datetime
from threading import Lock
from typing import Any, Literal

import anthropic
from fastapi import APIRouter, FastAPI, Header, HTTPException, Request, Response
from pydantic import Field, ValidationError
from sqlalchemy.engine import Engine

from .budget import JST, DailyLimitReached, consume_family_call, family_scope
from .guards import (
    INPUT_MAX_CHARS,
    THIRD_PARTY_NOTICE,
    check_input_length,
    medical_reply,
    unmask_aliases,
)
from .intake import (
    CANNOT_SORT_TEXT,
    DUE_MAX_CHARS,
    NewItem,
    build_item,
    find_trigger,
    intake,
    require_due_for_time,
)
from .ledger import Family, Item, Status, _Strict, status_label
from .presets import load_family_from_preset
from .retell import MissingKey, make_client
from .store import FamilyNotFound, VersionConflict, create_family, load_family, save_family

BOARD_NOT_FOUND = "ボードが見つかりません"
VERSION_CONFLICT = "ほかの端末で変わりました。読み直したので、もう一度押してください"
ITEM_NOT_FOUND = "項目が見つかりません"
ITEM_IN_USE = "案の根拠になっている項目は、まだ消せません"
AI_LIMIT_REACHED = "今日の回数の上限に届きました。入れた文は残っています"
SAVE_RETRIES = 3
REMEMBERED_REQUESTS = 2000


class NewBoard(_Strict):
    home: Literal["home_a", "home_b"]


class StatusChange(_Strict):
    version: int
    status: Status
    due: str | None = Field(default=None, max_length=DUE_MAX_CHARS)


class IntakeRequest(_Strict):
    """request_id は端末の「仕分け中」の番号。送り直しで同じ文を2回入れないため。"""

    request_id: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1)


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


def new_item_id() -> str:
    return f"i-{uuid.uuid4().hex[:12]}"


def now_jst() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def make_router(engine: Engine, client_factory: Any = make_client) -> APIRouter:
    router = APIRouter(prefix="/api")
    # 仕分け済みの番号。応答が届かず端末が送り直しても、2回入れない。
    # 1プロセスの記憶で足りる（再起動をまたぐ送り直しは、まれなので許す）。
    finished_requests: OrderedDict[tuple[str, str], None] = OrderedDict()
    finished_lock = Lock()

    def already_finished(key: tuple[str, str]) -> bool:
        with finished_lock:
            return key in finished_requests

    def remember_finished(key: tuple[str, str]) -> None:
        with finished_lock:
            finished_requests[key] = None
            while len(finished_requests) > REMEMBERED_REQUESTS:
                finished_requests.popitem(last=False)

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
            item = build_item(body, now_jst(), new_item_id())
        except (ValidationError, ValueError) as error:
            # 入力の中身を応答に写さない。
            raise HTTPException(status_code=422, detail="項目の形が正しくありません") from error
        family.items.append(item)
        return save(family, body.version)

    @router.post("/board/intake")
    def sort_text(
        body: IntakeRequest, x_family_code: str | None = Header(default=None)
    ) -> dict[str, Any]:
        """貼った文を仕分けて台帳に入れる。

        Claudeを待つ間に別の端末が版を進めることがある。足すだけの保存なので、
        画面に衝突を見せず、読み直して通す。
        """
        family = read(x_family_code)
        if not check_input_length(body.text):
            raise HTTPException(status_code=422, detail=f"{INPUT_MAX_CHARS}字までです")
        key = (family_scope(family.code), body.request_id)
        if already_finished(key):
            return {"board": board_view(family), "added": [], "trigger": None}
        try:
            result = intake(
                client_factory(),
                family,
                body.text,
                now_jst(),
                new_item_id,
                before_call=lambda: consume_family_call(engine, family.code),
            )
        except DailyLimitReached:
            raise HTTPException(status_code=429, detail=AI_LIMIT_REACHED) from None
        except (MissingKey, anthropic.APIError):
            # 応答に中身を写さない。
            raise HTTPException(status_code=503, detail=CANNOT_SORT_TEXT) from None
        if result.message:
            raise HTTPException(status_code=422, detail=result.message)
        for _ in range(SAVE_RETRIES):
            current = read(x_family_code)
            if already_finished(key):
                return {"board": board_view(current), "added": [], "trigger": None}
            current.items.extend(result.items)
            try:
                saved = save_family(engine, current, current.version)
            except VersionConflict:
                continue
            remember_finished(key)
            trigger = find_trigger(result.items, saved.items)
            return {
                "board": board_view(saved),
                "added": [item.id for item in result.items],
                "trigger": trigger[0] if trigger else None,
            }
        raise HTTPException(status_code=409, detail=VERSION_CONFLICT)

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


def install_api(app: FastAPI, engine: Engine, client_factory: Any = make_client) -> None:
    @app.middleware("http")
    async def no_store_api(request: Request, call_next: Any) -> Response:
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    app.include_router(make_router(engine, client_factory))
