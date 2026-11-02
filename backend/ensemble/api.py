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

from .ailog import forget_family
from .budget import JST, DailyLimitReached, consume_family_call, family_scope
from .guards import (
    CANNOT_PROPOSE_TEXT,
    INPUT_MAX_CHARS,
    check_branch,
    mask_aliases,
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
    stage_for,
)
from .ledger import (
    ActionType,
    Branch,
    Certainty,
    Family,
    Item,
    Kind,
    Part,
    Status,
    _Strict,
    status_label,
)
from .presets import load_family_from_preset
from .retell import MissingKey, make_client, retell
from .store import (
    FamilyNotFound,
    VersionConflict,
    create_family,
    delete_family,
    load_family,
    save_family,
)

BOARD_NOT_FOUND = "ボードが見つかりません"
VERSION_CONFLICT = "ほかの端末で変わりました。読み直したので、もう一度押してください"
ITEM_NOT_FOUND = "項目が見つかりません"
ITEM_IN_USE = "案の根拠になっている項目は、まだ消せません"
CANNOT_ACCEPT_TEXT = "この案は保存できません。読み直してください"
NOT_A_TRIGGER = "この項目からは案を作りません"
AI_LIMIT_REACHED = "今日の回数の上限に届きました。入れた文は残っています"
NOT_FOR_REPLY = "この項目には答えられません"
SAVE_RETRIES = 3
REMEMBERED_REQUESTS = 2000
# 家族の4択の答えと、台帳の状態（仕様§6）。
ANSWER_WITH_TIME = "◯時なら"
FAMILY_ANSWERS = {
    "持っていく": Status.ACCEPTED,
    "手配する": Status.ACCEPTED,
    "今日は難しい": Status.DIFFICULT,
    ANSWER_WITH_TIME: Status.OK_WITH_TIME,
}


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


class ProposalRequest(_Strict):
    item_id: str = Field(min_length=1, max_length=100)


class AcceptBranch(_Strict):
    version: int
    branch: Branch


class FamilyReply(_Strict):
    """家族の1件の答え。版は受け取らない（親の画面の更新と競わせない）。"""

    answer: Literal["持っていく", "手配する", "今日は難しい", "◯時なら"]
    when: Literal["今日の夜", "明日の朝", "明日の夜"] | None = None


def map_branch_text(branch: Branch, convert: Any) -> Branch:
    """分かれ道の中の、親が読む文だけを変換する（呼び名の伏せ字と戻し）。"""
    copied = branch.model_copy(deep=True)
    for outcome in copied.outcomes:
        outcome.label = convert(outcome.label)
        for action in outcome.actions:
            if action.draft:
                action.draft = convert(action.draft)
    return copied


def branch_view(branch: Branch, aliases: dict[str, str]) -> dict[str, Any]:
    return map_branch_text(branch, lambda text: unmask_aliases(text, aliases)).model_dump(
        mode="json"
    )


def board_view(family: Family) -> dict[str, Any]:
    """画面へ渡す形。家族コードは返さない。表示名と固定文はサーバーの1か所で決める。"""
    data = family.model_dump(mode="json", exclude={"code"})
    for shown, item in zip(data["items"], family.items):
        for field in ("text_original", "task_summary"):
            if shown[field]:
                shown[field] = unmask_aliases(shown[field], family.aliases)
        shown["status_label"] = (
            status_label(item.status, item.due, item.part) if item.status else None
        )
        shown["medical_reply"] = medical_reply(item)
        shown["notice"] = THIRD_PARTY_NOTICE if item.text_original is None else None
    data["branches"] = [branch_view(branch, family.aliases) for branch in family.branches]
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


def sent_item_id(branch_id: str, outcome: int, action: int) -> str:
    """案の連絡に対応する項目のID。台帳に欄を足さず、案の位置から決める。"""
    return f"s-{branch_id}-{outcome}-{action}"


def items_from_branch(branch: Branch, family: Family) -> list[Item]:
    """親が進めると決めた連絡を、未依頼の項目にする。

    家族の1件のリンクは、親が共有するより前に開ける先が要るので、ここで作る。
    病院には送らず親が直接聞くので、確認の項目にする。今ある項目の状態は変えない。
    """
    by_id = {item.id: item for item in family.items}
    errand = family.profile.errand_people
    made = []
    for o, outcome in enumerate(branch.outcomes):
        for a, action in enumerate(outcome.actions):
            if action.type == ActionType.NOTHING or not action.draft:
                continue
            basis = [by_id[basis_id] for basis_id in action.basis_ids]
            is_hospital = action.part == Part.HOSPITAL
            made.append(
                Item(
                    id=sent_item_id(branch.id, o, a),
                    kind=Kind.QUESTION if is_hospital else Kind.ACTION,
                    part=action.part,
                    text_original=action.draft,
                    speaker="親",
                    recorded_at=now_jst(),
                    # 確定の根拠が無ければ確定にしない（後の案の根拠になるため）。
                    certainty=(
                        Certainty.CONFIRMED
                        if any(item.certainty == Certainty.CONFIRMED for item in basis)
                        else Certainty.LIKELY
                    ),
                    status=Status.UNCONFIRMED if is_hospital else Status.TO_ASK,
                    owner=errand[0].label if action.part == Part.FAMILY and errand else None,
                    location=next(
                        (
                            item.location
                            for item in basis
                            if item.location and item.part == action.part
                        ),
                        None,
                    ),
                )
            )
    return made


def make_router(engine: Engine, client_factory: Any = make_client) -> APIRouter:
    router = APIRouter(prefix="/api")
    # 仕分け済みの番号と結果。応答が届かず端末が送り直しても、2回入れず、同じ結果を返す。
    # きっかけを返さないと、案を作る指示が失われるため。
    # 1プロセスの記憶で足りる（再起動をまたぐ送り直しは、まれなので許す）。
    finished_requests: OrderedDict[tuple[str, str], dict[str, Any]] = OrderedDict()
    finished_lock = Lock()

    def finished_result(key: tuple[str, str]) -> dict[str, Any] | None:
        with finished_lock:
            return finished_requests.get(key)

    def remember_finished(key: tuple[str, str], result: dict[str, Any]) -> None:
        with finished_lock:
            finished_requests[key] = result
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
        finished = finished_result(key)
        if finished:
            return {"board": board_view(family), **finished}
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
            finished = finished_result(key)
            if finished:
                return {"board": board_view(current), **finished}
            current.items.extend(result.items)
            try:
                saved = save_family(engine, current, current.version)
            except VersionConflict:
                continue
            except FamilyNotFound:
                # 仕分けの間にボードが消された。
                raise HTTPException(status_code=404, detail=BOARD_NOT_FOUND) from None
            trigger = find_trigger(result.items, saved.items)
            outcome = {
                "added": [item.id for item in result.items],
                "trigger": trigger[0] if trigger else None,
            }
            remember_finished(key, outcome)
            return {"board": board_view(saved), **outcome}
        raise HTTPException(status_code=409, detail=VERSION_CONFLICT)

    @router.post("/board/proposal")
    def make_proposal(
        body: ProposalRequest, x_family_code: str | None = Header(default=None)
    ) -> dict[str, Any]:
        """案を作って返すだけで、台帳には何も書かない。親が押す前は何も変わらない。

        案の控えは端末が持つ。台帳に「未承認」の欄を足さないため（スキーマは契約）。
        """
        family = read(x_family_code)
        item = next((entry for entry in family.items if entry.id == body.item_id), None)
        if item is None:
            raise HTTPException(status_code=404, detail=ITEM_NOT_FOUND)
        stage = stage_for(item, family.items)
        if stage is None:
            raise HTTPException(status_code=422, detail=NOT_A_TRIGGER)
        try:
            result = retell(
                client_factory(),
                family,
                item.id,
                stage,
                before_call=lambda: consume_family_call(engine, family.code),
            )
        except DailyLimitReached:
            raise HTTPException(status_code=429, detail=AI_LIMIT_REACHED) from None
        except (MissingKey, anthropic.APIError):
            raise HTTPException(status_code=503, detail=CANNOT_PROPOSE_TEXT) from None
        return {
            "item_id": item.id,
            "stage": stage,
            "branch": branch_view(result.branch, family.aliases) if result.branch else None,
            "message": result.message,
        }

    @router.post("/board/branches", status_code=201)
    def accept_branch(
        body: AcceptBranch, x_family_code: str | None = Header(default=None)
    ) -> dict[str, Any]:
        """親が［この案で進める］を押したときだけ台帳に入れる。

        案は端末から戻ってくるので、守りをもう一度すべて当てる。
        今ある項目の状態は変えない（送ったかどうかは親が決める）。連絡ごとに未依頼の項目を足す。
        """
        family = read(x_family_code)
        require_version(family, body.version)
        branch = map_branch_text(body.branch, lambda text: mask_aliases(text, family.aliases))
        branch.id = f"b-{uuid.uuid4().hex[:12]}"
        if check_branch(branch, family):
            raise HTTPException(status_code=422, detail=CANNOT_ACCEPT_TEXT)
        family.branches.append(branch)
        family.items.extend(items_from_branch(branch, family))
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

    @router.post("/board/items/{item_id}/reply")
    def family_reply(
        item_id: str, body: FamilyReply, x_family_code: str | None = Header(default=None)
    ) -> dict[str, Any]:
        """家族の1件の4択。答える人は版を知らないので、読み直して通す。"""
        with_time = body.answer == ANSWER_WITH_TIME
        if with_time and not body.when:
            raise HTTPException(status_code=422, detail=NOT_FOR_REPLY)
        status = FAMILY_ANSWERS[body.answer]
        for _ in range(SAVE_RETRIES):
            family = read(x_family_code)
            index = next((i for i, item in enumerate(family.items) if item.id == item_id), None)
            if index is None:
                raise HTTPException(status_code=404, detail=ITEM_NOT_FOUND)
            item = family.items[index]
            if (
                item.kind != Kind.ACTION
                or item.part != Part.FAMILY
                or item.status in (Status.DELIVERED, Status.DONE)
            ):
                raise HTTPException(status_code=422, detail=NOT_FOR_REPLY)
            if with_time:
                due = body.when
            else:
                due = None if item.status == Status.OK_WITH_TIME else item.due
            if item.status == status and item.due == due:
                return board_view(family)  # 同じ答えの押し直しで「新」を付けない
            family.items[index] = item.model_copy(update={"status": status, "due": due})
            try:
                return board_view(save_family(engine, family, family.version))
            except VersionConflict:
                continue
            except FamilyNotFound:
                raise HTTPException(status_code=404, detail=BOARD_NOT_FOUND) from None
        raise HTTPException(status_code=409, detail=VERSION_CONFLICT)

    @router.delete("/board")
    def delete_board(x_family_code: str | None = Header(default=None)) -> dict[str, bool]:
        """このボードを消す。同意の3行の約束。AIへの記録からも取り除く。"""
        if not x_family_code:
            raise HTTPException(status_code=404, detail=BOARD_NOT_FOUND)
        try:
            delete_family(engine, x_family_code)
        except FamilyNotFound:
            raise HTTPException(status_code=404, detail=BOARD_NOT_FOUND) from None
        forget_family(x_family_code)
        return {"deleted": True}

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
