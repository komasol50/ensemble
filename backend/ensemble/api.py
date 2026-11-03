"""ボードのAPI。家族コードはURLのパスにも問い合わせ文字列にも入れない。

アクセス記録にはパスが残るので、画面のURLは `/b#コード`（# より後ろはサーバーへ届かない）にし、
APIにはヘッダで渡す。どのURLも同じなので、応答は保存させない。
"""

import uuid
from datetime import date, datetime
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
from .impact import impact_view
from .intake import (
    CANNOT_SORT_TEXT,
    Choices,
    DUE_MAX_CHARS,
    NewItem,
    build_item,
    find_trigger,
    intake,
    require_due_for_time,
    stage_for,
)
from .ledger import (
    Action,
    ActionType,
    Branch,
    Certainty,
    Family,
    Item,
    Kind,
    Medical,
    Part,
    Status,
    _Strict,
    sent_item_id,
    status_label,
)
from .once import OnceCache
from .reading import (
    MAX_DOCS,
    MAX_DOCS_TOTAL_CHARS,
    MAX_IMAGE_CHARS,
    MEDIA_TYPES,
    READABLE_CATEGORIES,
    CANNOT_READ_TEXT,
    Doc,
    read_photo,
    search_docs,
)
from .presets import load_family_from_preset
from .retell import MissingKey, make_client, retell
from .week import week_view
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
CANNOT_ACCEPT_TEXT = "この案は保存できません。読み直してください"
NOT_A_TRIGGER = "この項目からは案を作りません"
AI_LIMIT_REACHED = "今日の回数の上限に届きました。入れた文は残っています"
NOT_FOR_REPLY = "この項目には答えられません"
NOT_READABLE = "この写真は読み取れません"
NOT_SEARCHABLE = "この項目は保管庫から探せません"
SAVE_RETRIES = 3
REMEMBERED_REQUESTS = 2000
REMEMBERED_PROPOSALS = 200
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
    said: Literal["言われた", "聞きたい", "頼みたい", "気づいた"] | None = None
    who: Literal[Part.HOSPITAL, Part.FAMILY, Part.CHILDCARE, Part.WORK, Part.SUPPORT] | None = None


class ProposalRequest(_Strict):
    item_id: str = Field(min_length=1, max_length=100)


class AcceptBranch(_Strict):
    version: int
    branch: Branch


class ReadPhotoRequest(_Strict):
    """読ませると親が選んだ写真。保存しない。"""

    image: str = Field(min_length=1)
    media_type: str
    category: str


class SearchRequest(_Strict):
    """端末が持っている読み取り済みの文。保存しない。"""

    docs: list[Doc]


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
    data["week"] = week_view(family, today_jst())
    data["impact"] = impact_view(family)
    return data


def withdraw_item(item_id: str, family: Family) -> None:
    """項目を消し、それを根拠にした案も取り下げる。

    誤りは「消す」で直すので、根拠の項目も消せる。条件の項目が消えたら分かれ道ごと、
    根拠が消えた案はその案だけを取り下げる。案の位置で項目のIDを決めているので、
    取り下げた案は抜かずに「何もしない」にする。
    送っていない連絡の項目は一緒に消し、送った連絡の項目は事実として残す。
    """
    unsent: set[str] = set()
    kept: list[Branch] = []
    for branch in family.branches:
        whole = branch.condition_item_id == item_id
        for o, outcome in enumerate(branch.outcomes):
            for a, action in enumerate(outcome.actions):
                action.release = [other for other in action.release if other != item_id]
                if whole or item_id in action.basis_ids:
                    unsent.add(sent_item_id(branch.id, o, a))
                    outcome.actions[a] = Action(part=action.part, type=ActionType.NOTHING)
        has_action = any(
            action.type != ActionType.NOTHING
            for outcome in branch.outcomes
            for action in outcome.actions
        )
        if has_action:
            kept.append(branch)
    family.branches = kept
    remaining = []
    for item in family.items:
        is_unsent = item.id in unsent and item.status in (Status.TO_ASK, Status.UNCONFIRMED)
        if item.id == item_id or is_unsent:
            continue
        if item.refers_to == item_id:
            item = item.model_copy(update={"refers_to": None})
        remaining.append(item)
    family.items = remaining


def new_item_id() -> str:
    return f"i-{uuid.uuid4().hex[:12]}"


def now_jst() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def today_jst() -> date:
    return datetime.now(JST).date()


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
    # 送り直しでAIを2回呼ばず、同じ結果を返す。きっかけが失われると案が作られないため。
    intake_calls = OnceCache(REMEMBERED_REQUESTS)
    proposal_calls = OnceCache(REMEMBERED_PROPOSALS)

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
        preset = load_family_from_preset(body.home)
        # 週の表は記録の日から曜日を置くので、ボードを作った日の記録にする。
        now = now_jst()
        preset.items = [item.model_copy(update={"recorded_at": now}) for item in preset.items]
        family = create_family(engine, preset)
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

        def sort_and_save() -> dict[str, Any]:
            try:
                result = intake(
                    client_factory(),
                    family,
                    body.text,
                    now_jst(),
                    new_item_id,
                    before_call=lambda: consume_family_call(engine, family.code),
                    choices=Choices(said=body.said, who=body.who),
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
                current.items.extend(result.items)
                try:
                    saved = save_family(engine, current, current.version)
                except VersionConflict:
                    continue
                except FamilyNotFound:
                    # 仕分けの間にボードが消された。
                    raise HTTPException(status_code=404, detail=BOARD_NOT_FOUND) from None
                trigger = find_trigger(result.items, saved.items)
                return {
                    "added": [item.id for item in result.items],
                    "trigger": trigger[0] if trigger else None,
                }
            raise HTTPException(status_code=409, detail=VERSION_CONFLICT)

        key = (family_scope(family.code), body.request_id)
        outcome = intake_calls.run(key, sort_and_save)
        return {"board": board_view(read(x_family_code)), **outcome}

    @router.post("/board/proposal")
    def make_proposal(
        body: ProposalRequest, x_family_code: str | None = Header(default=None)
    ) -> dict[str, Any]:
        """案を作って返すだけで、台帳には何も書かない。親が押す前は何も変わらない。

        案の控えは端末が持つ。台帳に「未承認」の欄を足さないため（スキーマは契約）。
        同じ版の台帳への同じ要求は、送り直しなのでAIを呼び直さない。
        """
        family = read(x_family_code)
        item = next((entry for entry in family.items if entry.id == body.item_id), None)
        if item is None:
            raise HTTPException(status_code=404, detail=ITEM_NOT_FOUND)
        stage = stage_for(item, family.items)
        if stage is None:
            raise HTTPException(status_code=422, detail=NOT_A_TRIGGER)

        def propose() -> dict[str, Any]:
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
                # 押す前の案を週の表で見せる。台帳には書かない。
                "week": week_view(family, today_jst(), result.branch) if result.branch else None,
            }

        return proposal_calls.run((family_scope(family.code), item.id, family.version), propose)

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

    @router.post("/photo/read")
    def read_a_photo(
        body: ReadPhotoRequest, x_family_code: str | None = Header(default=None)
    ) -> dict[str, Any]:
        """写真を読み取って返すだけで、写真も文も保存しない。検査の結果は読まない。"""
        family = read(x_family_code)
        if (
            body.category not in READABLE_CATEGORIES
            or body.media_type not in MEDIA_TYPES
            or len(body.image) > MAX_IMAGE_CHARS
        ):
            raise HTTPException(status_code=422, detail=NOT_READABLE)
        try:
            result = read_photo(
                client_factory(),
                family,
                body.image,
                body.media_type,
                before_call=lambda: consume_family_call(engine, family.code),
            )
        except DailyLimitReached:
            raise HTTPException(status_code=429, detail=AI_LIMIT_REACHED) from None
        except (MissingKey, anthropic.APIError):
            raise HTTPException(status_code=503, detail=CANNOT_READ_TEXT) from None
        return {"text": result.text, "refused": result.refused, "message": result.message}

    @router.post("/board/items/{item_id}/search")
    def search_the_vault(
        item_id: str, body: SearchRequest, x_family_code: str | None = Header(default=None)
    ) -> dict[str, Any]:
        """親の質問に答える箇所を資料から探す。答えは資料の文の引用だけ。保存しない。

        判断を求める問いは探さず、これまでどおり固定文を返す。
        """
        family = read(x_family_code)
        item = next((entry for entry in family.items if entry.id == item_id), None)
        if item is None:
            raise HTTPException(status_code=404, detail=ITEM_NOT_FOUND)
        question = item.text_original or item.task_summary
        if (
            item.kind != Kind.QUESTION
            or item.medical == Medical.ASK
            or item.part not in (Part.HOSPITAL, Part.PARENT)
            or not question
        ):
            raise HTTPException(status_code=422, detail=NOT_SEARCHABLE)
        if (
            not body.docs
            or len(body.docs) > MAX_DOCS
            or sum(len(doc.text) for doc in body.docs) > MAX_DOCS_TOTAL_CHARS
            or any(doc.category not in READABLE_CATEGORIES for doc in body.docs)
        ):
            raise HTTPException(status_code=422, detail=NOT_SEARCHABLE)
        try:
            result = search_docs(
                client_factory(),
                family,
                question,
                body.docs,
                before_call=lambda: consume_family_call(engine, family.code),
            )
        except DailyLimitReached:
            raise HTTPException(status_code=429, detail=AI_LIMIT_REACHED) from None
        except (MissingKey, anthropic.APIError):
            raise HTTPException(status_code=503, detail=NOT_SEARCHABLE) from None
        return {"found": result.found, "doc_id": result.doc_id, "quote": result.quote}

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
        withdraw_item(item_id, family)
        return save(family, version)

    return router


def install_api(app: FastAPI, engine: Engine, client_factory: Any = make_client) -> None:
    @app.middleware("http")
    async def no_store_api(request: Request, call_next: Any) -> Response:
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        # URLの # より後ろは送られないが、外へ出るときの参照元も残さない。検索にも載せない。
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    app.include_router(make_router(engine, client_factory))
