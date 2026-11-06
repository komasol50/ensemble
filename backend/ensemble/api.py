"""ボードのAPI。家族コードはURLのパスにも問い合わせ文字列にも入れない。

アクセス記録にはパスが残るので、画面のURLは `/b#コード`（# より後ろはサーバーへ届かない）にし、
APIにはヘッダで渡す。どのURLも同じなので、応答は保存させない。
"""

import os
import time
import uuid
from collections import defaultdict, deque
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any, Literal

import anthropic
from fastapi import APIRouter, FastAPI, Header, HTTPException, Request, Response
from pydantic import Field, ValidationError
from sqlalchemy.engine import Engine

from .ailog import forget_family
from .budget import JST, DailyLimitReached, consume_family_call, family_scope
from .edit import (
    CANNOT_SEND_MEDICAL_TEXT,
    EDITED_LABEL,
    UNDOABLE_STATUSES,
    UNDONE_LABEL,
    EditRefused,
    ItemEdit,
    apply_edit,
    event_label,
    first_label,
    is_from_proposal,
    is_outgoing,
    original_fixed_reason,
    undo_target,
)
from .guards import (
    find_medical_words,
    find_symptom_words,
    words_in_text,
    MEDICAL_FIXED_TEXT,
    keep_true_quotes,
    CANNOT_PROPOSE_TEXT,
    INPUT_MAX_CHARS,
    MEDICAL_WORD_ERROR,
    check_branch,
    mask_aliases,
    THIRD_PARTY_NOTICE,
    check_input_length,
    medical_reply,
    unmask_aliases,
)
from .impact import impact_view, short_text
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
from .removal import restore_item, withdraw_item
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
from .presets import (
    OWN_HOME_NAME,
    load_family_from_preset,
    new_own_family,
    profile_from_choices,
    spaced_records,
)
from .retell import MissingKey, make_client, retell
from .week import calendar_days, doctor_view, week_view
from .store import (
    FamilyNotFound,
    ItemEvent,
    VersionConflict,
    create_family,
    add_trash,
    delete_family,
    forget_events,
    get_trash,
    list_events,
    list_item_events,
    list_trash,
    load_family,
    remove_trash,
    save_family,
)

BOARD_NOT_FOUND = "記録が見つかりません"
VERSION_CONFLICT = "もう1台で変わったので読み直しました。もう一度押してください"
ITEM_NOT_FOUND = "項目が見つかりません"
CANNOT_ACCEPT_TEXT = "この案は保存できません。読み直してください"
TOO_MANY_BOARDS = "記録を始める回数の上限です。しばらくしてから試してください"
DEFAULT_BOARD_CREATE_LIMIT = 30
BOARD_WINDOW_SECONDS = 3600
NOT_A_TRIGGER = "この項目からは案を作りません"
AI_LIMIT_REACHED = "今日の回数の上限に届きました。入れた文は残っています"
NOT_FOR_REPLY = "この項目には答えられません"
TRASH_NOT_FOUND = "ゴミ箱にありません"
NOT_READABLE = "この写真は読み取れません"
NOT_SEARCHABLE = "この項目は写真から探せません"
SAVE_RETRIES = 3
REMEMBERED_REQUESTS = 2000
REMEMBERED_PROPOSALS = 200
# 家族の4択の答えと、台帳の状態（仕様§6）。
ANSWER_WITH_TIME = "◯時なら"
ANSWER_TALK = "時間を相談したい"
FAMILY_ANSWERS = {
    "持っていく": Status.ACCEPTED,
    "手配する": Status.ACCEPTED,
    "今日は難しい": Status.DIFFICULT,
    "今日はむずかしい": Status.DIFFICULT,
    ANSWER_TALK: Status.ON_HOLD,
    ANSWER_WITH_TIME: Status.OK_WITH_TIME,
}


class SetupChoices(_Strict):
    """自分の家庭の設定。全部選ぶ形で、自由な文字を受けない（呼び名を伏せる仕組みが効かないため）。"""

    parent: Literal["母", "父", "その他"] | None = None
    adult: Literal["いる", "いない"] | None = None
    child: Literal["いる", "いない"] | None = None
    school: Literal["保育園", "学校", "なし"] | None = None
    work: Literal["会社員", "自営業", "その他"] | None = None
    errand: Literal["夫・パートナー", "祖父母", "いない"] | None = None
    errand_time: Literal["平日の夜と朝", "休みの日だけ", "日中も動ける"] | None = None
    bedside: Literal["いる", "いない"] | None = None
    supports: list[Literal["延長保育", "ファミサポ", "一時預かり"]] = Field(default_factory=list)
    no_support: bool = False


class NewBoard(_Strict):
    home: Literal["home_a", "home_b", "shoot_a", "own"]
    choices: SetupChoices | None = None


class SetupRequest(_Strict):
    version: int
    choices: SetupChoices


NOT_FOR_THIS_ITEM = "この項目には使えません"


class StatusChange(_Strict):
    version: int
    status: Status
    due: str | None = Field(default=None, max_length=DUE_MAX_CHARS)


class UndoRequest(_Strict):
    version: int


class IntakeRequest(_Strict):
    """request_id は端末の「仕分け中」の番号。送り直しで同じ文を2回入れないため。"""

    request_id: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1)
    said: Literal["言われた", "聞きたい", "頼みたい", "気づいた"] | None = None
    who: Literal[Part.HOSPITAL, Part.FAMILY, Part.CHILDCARE, Part.WORK, Part.SUPPORT] | None = None


class ProposalRequest(_Strict):
    item_id: str = Field(min_length=1, max_length=100)
    # 親が説明を聞いたあとの画面で選んだ医師の言葉の節。選択肢にあるものだけを受ける。
    chosen: str | None = Field(default=None, max_length=500)


class AcceptBranch(_Strict):
    version: int
    branch: Branch


class ReadPhotoRequest(_Strict):
    """読ませると親が選んだ写真。保存しない。"""

    image: str = Field(min_length=1)
    media_type: str
    category: str


class FreeSearchRequest(_Strict):
    """自由な検索欄。問いも資料も保存しない。"""

    question: str = Field(min_length=1, max_length=200)
    docs: list[Doc]


class SearchRequest(_Strict):
    """端末が持っている読み取り済みの文。保存しない。"""

    docs: list[Doc]


NOTE_MAX_CHARS = 200
ONE_NOTE_PREFIX = "n-"  # 夫のひとことの項目の印。案から作った連絡の s- と同じ考え


class FamilyReply(_Strict):
    """家族の1件の答え。版は受け取らない（親の画面の更新と競わせない）。"""

    answer: Literal[
        "持っていく", "手配する", "今日は難しい", "今日はむずかしい", "時間を相談したい", "◯時なら"
    ]
    when: Literal["今日の夜", "明日の朝", "明日の夜"] | None = None
    # ひとこと。1つの用事に1つで、続きは作らない。別の項目で持つ（Plan main の条件4）。
    note: str | None = Field(default=None, max_length=NOTE_MAX_CHARS)


def map_branch_text(branch: Branch, convert: Any) -> Branch:
    """分かれ道の中の、親が読む文だけを変換する（呼び名の伏せ字と戻し）。"""
    copied = branch.model_copy(deep=True)
    for outcome in copied.outcomes:
        outcome.label = convert(outcome.label)
        if outcome.clause:
            outcome.clause = convert(outcome.clause)
        for step in outcome.next:
            step.day = convert(step.day)
            step.clause = convert(step.clause)
        for action in outcome.actions:
            if action.draft:
                action.draft = convert(action.draft)
            if action.ask:
                action.ask = convert(action.ask)
    return copied


def branch_view(branch: Branch, aliases: dict[str, str]) -> dict[str, Any]:
    return map_branch_text(branch, lambda text: unmask_aliases(text, aliases)).model_dump(
        mode="json"
    )


def released_items(family: Family) -> dict[str, str]:
    """取り消しの連絡を親が送ったと記録した項目と、その連絡のID。台帳の状態は変えず、表示だけ変える。"""
    by_id = {item.id: item for item in family.items}
    released: dict[str, str] = {}
    for branch in family.branches:
        for o, outcome in enumerate(branch.outcomes):
            for a, action in enumerate(outcome.actions):
                made = by_id.get(sent_item_id(branch.id, o, a))
                if made is not None and made.status != Status.TO_ASK:
                    released.update(dict.fromkeys(action.release, made.id))
    return released


def outdated_item_ids(family: Family) -> set[str]:
    """後の案で取り消された日を含む、前の案の連絡。文を読むのはコードで、AIは呼ばない。

    隠さず、状態も変えない。古い連絡の中の用事はまだ生きていることがあるので、夫の画面で一言添えるだけ。
    """
    by_id = {item.id: item for item in family.items}
    sent_earlier: list[Item] = []
    outdated: set[str] = set()
    for branch in family.branches:
        days = [day.day for day in branch.candidate_days if day.cancelled]
        outdated |= {
            item.id
            for item in sent_earlier
            if any(day in (item.text_original or "") for day in days)
        }
        sent_earlier += [
            by_id[sent_item_id(branch.id, o, a)]
            for o, outcome in enumerate(branch.outcomes)
            for a in range(len(outcome.actions))
            if sent_item_id(branch.id, o, a) in by_id
        ]
    return outdated


def superseded_item_ids(family: Family) -> set[str]:
    """新しい案ができた後も、まだ送っていない前の案の連絡。状態は変えず、親が決める。

    親が家族に頼みたいと入れた文も、後の案の同じ相手への文がそれを根拠に引き継いだら、前の文として
    扱う（図の⑦：夫への文は1通。「着替えも頼みたい」は案の文に入っている）。
    """
    earlier = {
        sent_item_id(branch.id, o, a)
        for branch in family.branches[:-1]
        for o, outcome in enumerate(branch.outcomes)
        for a in range(len(outcome.actions))
    }
    carried = {
        (action.part, basis_id)
        for branch in family.branches
        for outcome in branch.outcomes
        for action in outcome.actions
        if action.type != ActionType.NOTHING and action.draft
        for basis_id in action.basis_ids
    }
    return {
        item.id
        for item in family.items
        if item.status == Status.TO_ASK
        and (
            item.id in earlier
            or (
                (item.part, item.id) in carried and is_outgoing(item) and not is_from_proposal(item)
            )
        )
    }


def board_view(
    family: Family, events: dict[str, list[dict[str, str]]] | None = None
) -> dict[str, Any]:
    """画面へ渡す形。家族コードは返さない。表示名と固定文はサーバーの1か所で決める。"""
    data = family.model_dump(mode="json", exclude={"code"})
    events = events or {}
    released = released_items(family)
    outdated = outdated_item_ids(family)
    superseded = superseded_item_ids(family)
    asks = asks_of(family)
    for shown, item in zip(data["items"], family.items):
        for field in ("text_original", "task_summary"):
            if shown[field]:
                shown[field] = unmask_aliases(shown[field], family.aliases)
        shown["status_label"] = (
            status_label(item.status, item.due, item.part) if item.status else None
        )
        shown["medical_reply"] = medical_reply(item)
        shown["notice"] = THIRD_PARTY_NOTICE if item.text_original is None else None
        shown["released"] = item.id in released
        shown["released_by"] = released.get(item.id)
        shown["short"] = unmask_aliases(short_text(item), family.aliases)
        ask = asks.get(item.id)
        # 親が文を直して頼みごとの文が消えたら、題に出さない。
        shown["ask"] = (
            unmask_aliases(ask, family.aliases) if words_in_text(ask, item.text_original) else None
        )
        shown["outdated"] = item.id in outdated
        shown["superseded"] = item.id in superseded
        shown["original_fixed"] = original_fixed_reason(family, item)
        # 入れた時刻は台帳にあり、その後に起きたことは別の表にある。無い時刻は作らない。
        first = [{"at": item.recorded_at, "label": first_label(item)}] if item.recorded_at else []
        shown["events"] = first + events.get(item.id, [])
    data["branches"] = [branch_view(branch, family.aliases) for branch in family.branches]
    data["week"] = week_view(family, today_jst())
    data["calendar"] = week_view(family, today_jst(), days=calendar_days(today_jst()))
    data["impact"] = impact_view(family)
    data["doctor"] = doctor_view(family, today_jst())
    # 画面の今日はサーバーの今日に揃える。端末の時計と食い違わせない。
    data["today"] = today_jst().isoformat()
    return data


def preview_impact(family: Family, branch: Branch) -> dict[str, Any] | None:
    """親が押す前の影響。控えのコピーに案を足して計算する。台帳は変えない。"""
    copied = family.model_copy(deep=True)
    copied.branches.append(branch)
    copied.items.extend(items_from_branch(branch, copied))
    return impact_view(copied)


def asks_of(family: Family) -> dict[str, str]:
    """案から作った連絡の、頼みごとの引用。"""
    return {
        sent_item_id(branch.id, o, a): action.ask
        for branch in family.branches
        for o, outcome in enumerate(branch.outcomes)
        for a, action in enumerate(outcome.actions)
        if action.ask
    }


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


def status_event(before: Item, label: str) -> ItemEvent:
    """状態を変えた記録。変わる前の状態と期日も残す（元に戻す先を決めるため）。"""
    status = str(before.status) if before.status is not None else None
    return ItemEvent(before.id, now_jst(), label, status, before.due)


def with_status(item: Item, status: Status, due: str | None) -> Item:
    """状態と期日を変えた項目。model_copy は検査をしないので、型を通し直す。"""
    try:
        changed = Item.model_validate(
            {**item.model_dump(mode="json"), "status": status, "due": due}
        )
        require_due_for_time(changed)
    except (ValidationError, ValueError) as error:
        raise HTTPException(status_code=422, detail=NOT_FOR_THIS_ITEM) from error
    return changed


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

    def view(family: Family) -> dict[str, Any]:
        return board_view(family, list_events(engine, family.code))

    def save(family: Family, version: int, events: Sequence[ItemEvent] = ()) -> dict[str, Any]:
        """画面が見ていた版で照合する（読み直した版を使うと、衝突に気づけない）。"""
        try:
            return view(save_family(engine, family, version, events))
        except VersionConflict:
            raise HTTPException(status_code=409, detail=VERSION_CONFLICT) from None
        except FamilyNotFound:
            raise HTTPException(status_code=404, detail=BOARD_NOT_FOUND) from None

    board_times: dict[str, deque[float]] = defaultdict(deque)

    def limit_board_creation(request: Request) -> None:
        """同じ接続元からのボード作成を、1時間あたりの回数で止める。再起動で数え直す。"""
        forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        source = forwarded or (request.client.host if request.client else "unknown")
        now = time.monotonic()
        times = board_times[source]
        while times and now - times[0] > BOARD_WINDOW_SECONDS:
            times.popleft()
        limit = int(os.environ.get("BOARD_CREATE_LIMIT", DEFAULT_BOARD_CREATE_LIMIT))
        if len(times) >= limit:
            raise HTTPException(status_code=429, detail=TOO_MANY_BOARDS)
        times.append(now)

    @router.post("/boards", status_code=201)
    def make_board(body: NewBoard, request: Request) -> dict[str, Any]:
        limit_board_creation(request)
        if body.home == "own":
            own = new_own_family(body.choices.model_dump() if body.choices else None)
            family = create_family(engine, own)
            return {"code": family.code, "board": view(family)}
        preset = load_family_from_preset(body.home)
        # 週の表は記録の日から曜日を置くので、ボードを作った日の記録にする。
        preset.items = spaced_records(preset.items, now_jst())
        family = create_family(engine, preset)
        return {"code": family.code, "board": view(family)}

    @router.put("/board/setup")
    def change_setup(
        body: SetupRequest, x_family_code: str | None = Header(default=None)
    ) -> dict[str, Any]:
        """自分の家庭の設定を直す。次のAIの呼び出しから効く。プリセットの家庭は変えない。"""
        family = read(x_family_code)
        require_version(family, body.version)
        if family.profile.name != OWN_HOME_NAME:
            raise HTTPException(status_code=422, detail=NOT_FOR_THIS_ITEM)
        family.profile = profile_from_choices(body.choices.model_dump())
        return save(family, body.version)

    @router.get("/board")
    def get_board(x_family_code: str | None = Header(default=None)) -> dict[str, Any]:
        return view(read(x_family_code))

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
        return {"board": view(read(x_family_code)), **outcome}

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

        # 選択肢にある節だけを、親の選択として渡す。
        doctor = doctor_view(family, today_jst())
        labels = [option["label"] for option in doctor["options"]] if doctor else []
        chosen = body.chosen if body.chosen in labels else None

        def propose() -> dict[str, Any]:
            try:
                result = retell(
                    client_factory(),
                    family,
                    item.id,
                    stage,
                    before_call=lambda: consume_family_call(engine, family.code),
                    today=today_jst(),
                    history=list_events(engine, family.code),
                    chosen=chosen,
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
                # 押す前の案を週の表と線で見せる。台帳には書かない。
                "week": week_view(family, today_jst(), result.branch) if result.branch else None,
                "impact": preview_impact(family, result.branch) if result.branch else None,
            }

        return proposal_calls.run(
            (family_scope(family.code), item.id, family.version, chosen), propose
        )

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
        # 親は文を直せるので、日付と言葉の守りはAIの文を作ったときに済ませている。
        violations = check_branch(branch, family, parent_edited=True)
        if any(MEDICAL_WORD_ERROR in violation for violation in violations):
            raise HTTPException(status_code=422, detail=CANNOT_SEND_MEDICAL_TEXT)
        if violations:
            raise HTTPException(status_code=422, detail=CANNOT_ACCEPT_TEXT)
        # 親が直した文にも引用を確かめ直す。合わなければ今の表示へ戻る。
        branch = keep_true_quotes(branch, family)
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
            changed = with_status(item, body.status, due)
            family.items[index] = changed
            # 押し直しで同じ時刻の記録を重ねない。
            events = (
                [status_event(item, event_label(changed))]
                if (item.status, item.due) != (changed.status, changed.due)
                else []
            )
            return save(family, body.version, events)
        raise HTTPException(status_code=404, detail=ITEM_NOT_FOUND)

    @router.post("/board/items/{item_id}/undo")
    def undo_status(
        item_id: str, body: UndoRequest, x_family_code: str | None = Header(default=None)
    ) -> dict[str, Any]:
        """済と返事を、記録にある1つ前の状態へ戻す。AIは呼ばない。

        もとの語を出すと今また返事が来たように読めるので、「元に戻した」と残す。
        """
        family = read(x_family_code)
        require_version(family, body.version)
        index = next((i for i, item in enumerate(family.items) if item.id == item_id), None)
        if index is None:
            raise HTTPException(status_code=404, detail=ITEM_NOT_FOUND)
        item = family.items[index]
        if item.status not in UNDOABLE_STATUSES:
            raise HTTPException(status_code=422, detail=NOT_FOR_THIS_ITEM)
        status, due = undo_target(item, list_item_events(engine, family.code, item_id))
        family.items[index] = with_status(item, status, due)
        return save(family, body.version, [status_event(item, UNDONE_LABEL)])

    @router.patch("/board/items/{item_id}/content")
    def edit_item(
        item_id: str, body: ItemEdit, x_family_code: str | None = Header(default=None)
    ) -> dict[str, Any]:
        """親が文・相手・担当・期日を直す。AIは呼ばない。送った後の文は直せない。"""
        family = read(x_family_code)
        require_version(family, body.version)
        if not any(item.id == item_id for item in family.items):
            raise HTTPException(status_code=404, detail=ITEM_NOT_FOUND)
        try:
            changed = apply_edit(family, item_id, body)
        except EditRefused as refused:
            raise HTTPException(status_code=422, detail=str(refused)) from refused
        if not changed:
            return view(family)  # 何も変えていなければ、版も記録も進めない
        return save(family, body.version, [ItemEvent(item_id, now_jst(), EDITED_LABEL)])

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
                or item.id in released_items(family)
            ):
                raise HTTPException(status_code=422, detail=NOT_FOR_REPLY)
            if with_time:
                due = body.when
            else:
                due = None if item.status == Status.OK_WITH_TIME else item.due
            # 送らなかったときは前のひとことのまま。空を送ったときだけ消す。
            keep_note = body.note is None
            note = (body.note or "").strip()
            # ひとことは id の印で見分ける。親が入れた記録を、同じ形だからと消さない。
            earlier = [
                other
                for other in family.items
                if other.refers_to == item_id and other.id.startswith(ONE_NOTE_PREFIX)
            ]
            same_note = keep_note or (earlier[0].text_original if earlier else "") == mask_aliases(
                note, family.aliases
            )
            if item.status == status and item.due == due and same_note:
                return view(family)  # 同じ答えの押し直しで「新」を付けない
            changed = item.model_copy(update={"status": status, "due": due})
            family.items[index] = changed
            if not same_note:
                # 前のひとことは置き換える。取り込みと同じ守りを通す。経緯の語には文を入れない。
                family.items = [other for other in family.items if other not in earlier]
                if note:
                    family.items.append(
                        build_item(
                            NewItem(
                                version=family.version,
                                kind=Kind.INFO,
                                part=Part.FAMILY,
                                certainty=Certainty.LIKELY,
                                text_original=mask_aliases(note, family.aliases),
                                speaker=item.owner or "家族",
                                refers_to=item_id,
                            ),
                            now_jst(),
                            f"{ONE_NOTE_PREFIX}{new_item_id()}",
                        )
                    )
            # 「時間を相談したい」は夫の答えの語のまま残す。親が記録した保留と分ける。
            label = event_label(changed)
            if body.answer == ANSWER_TALK:
                label = f"{changed.owner}：{ANSWER_TALK}" if changed.owner else ANSWER_TALK
            event = status_event(item, label)
            try:
                return view(save_family(engine, family, family.version, [event]))
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

    @router.post("/board/search")
    def search_freely(
        body: FreeSearchRequest, x_family_code: str | None = Header(default=None)
    ) -> dict[str, Any]:
        """自由な問いで資料を探す。問いは台帳に入れない。

        先に取り込みと同じ仕分けを通し、医療の問いなら固定文だけを返して探さない。
        答えは資料の文の引用だけで、AIの要約を足さない。
        """
        family = read(x_family_code)
        if (
            not body.docs
            or len(body.docs) > MAX_DOCS
            or sum(len(doc.text) for doc in body.docs) > MAX_DOCS_TOTAL_CHARS
            or any(doc.category not in READABLE_CATEGORIES for doc in body.docs)
        ):
            raise HTTPException(status_code=422, detail=NOT_SEARCHABLE)
        # 語に当たる問いは、AIを呼ばずに固定文だけを返す。「見つかりません」と言わない。
        if find_medical_words(body.question) or find_symptom_words(body.question):
            return {"medical": MEDICAL_FIXED_TEXT, "found": False}
        count_call = lambda: consume_family_call(engine, family.code)
        try:
            sorted_result = intake(
                client_factory(),
                family,
                body.question,
                now_jst(),
                new_item_id,
                before_call=count_call,
                choices=Choices(said="聞きたい", who=Part.HOSPITAL),
            )
            if any(item.medical == Medical.ASK for item in sorted_result.items):
                return {"medical": MEDICAL_FIXED_TEXT, "found": False}
            if sorted_result.message:
                raise HTTPException(status_code=422, detail=sorted_result.message)
            result = search_docs(
                client_factory(), family, body.question, body.docs, before_call=count_call
            )
        except DailyLimitReached:
            raise HTTPException(status_code=429, detail=AI_LIMIT_REACHED) from None
        except (MissingKey, anthropic.APIError):
            raise HTTPException(status_code=503, detail=NOT_SEARCHABLE) from None
        return {
            "medical": None,
            "found": result.found,
            "doc_id": result.doc_id,
            "quote": result.quote,
        }

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
        undo = withdraw_item(item_id, family)
        # 先にゴミ箱へ入れる。台帳の保存が失敗したら取り消す（項目を失わないため）。
        trash_id = f"t-{uuid.uuid4().hex[:12]}"
        add_trash(engine, family.code, trash_id, undo)
        try:
            return save(family, version)
        except HTTPException:
            remove_trash(engine, family.code, trash_id)
            raise

    @router.get("/board/trash")
    def read_trash(x_family_code: str | None = Header(default=None)) -> dict[str, Any]:
        family = read(x_family_code)
        entries = []
        for trash_id, undo in list_trash(engine, family.code):
            item = Item.model_validate(undo["item"])
            text = item.task_summary or item.text_original or ""
            entries.append(
                {
                    "id": trash_id,
                    "item_id": item.id,
                    "part": "自分のこと" if item.part == Part.PARENT else item.part.value,
                    "text": unmask_aliases(text, family.aliases),
                    "status_label": (
                        status_label(item.status, item.due, item.part) if item.status else None
                    ),
                    "withdrew": bool(undo["actions"] or undo["branches"]),
                }
            )
        return {"entries": entries}

    @router.post("/board/trash/{trash_id}/restore")
    def restore_from_trash(
        trash_id: str, x_family_code: str | None = Header(default=None)
    ) -> dict[str, Any]:
        """戻す。別の端末が版を進めていても、戻す意思は変わらないので読み直して通す。"""
        family = read(x_family_code)
        undo = get_trash(engine, family.code, trash_id)
        if undo is None:
            raise HTTPException(status_code=404, detail=TRASH_NOT_FOUND)
        for _ in range(SAVE_RETRIES):
            current = read(x_family_code)
            restore_item(undo, current)
            try:
                saved = save_family(engine, current, current.version)
            except VersionConflict:
                continue
            remove_trash(engine, family.code, trash_id)
            return view(saved)
        raise HTTPException(status_code=409, detail=VERSION_CONFLICT)

    @router.delete("/board/trash/{trash_id}")
    def purge_from_trash(
        trash_id: str, x_family_code: str | None = Header(default=None)
    ) -> dict[str, bool]:
        """完全に消す。元に戻せない。"""
        family = read(x_family_code)
        if not remove_trash(engine, family.code, trash_id):
            raise HTTPException(status_code=404, detail=TRASH_NOT_FOUND)
        # 完全に消した項目の時刻も残さない。ゴミ箱に残っている項目の分は、戻せるように残す。
        keep = {item.id for item in family.items}
        for _, undo in list_trash(engine, family.code):
            keep.add(undo["item"]["id"])
            keep |= {entry["id"] for entry in undo["also_removed"]}
        forget_events(engine, family.code, keep)
        return {"purged": True}

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
