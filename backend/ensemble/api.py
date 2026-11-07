"""ボードのAPI。家族コードはURLのパスにも問い合わせ文字列にも入れない。

アクセス記録にはパスが残るので、画面のURLは `/b#コード`（# より後ろはサーバーへ届かない）にし、
APIにはヘッダで渡す。どのURLも同じなので、応答は保存させない。
"""

import json
import os
import time
import uuid
from collections import defaultdict, deque
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any, Literal

import anthropic
from fastapi import APIRouter, FastAPI, Header, HTTPException, Request, Response
from pydantic import Field, StrictInt, ValidationError, field_validator
from sqlalchemy.engine import Engine

from .ailog import forget_family
from .budget import JST, DailyLimitReached, consume_family_call, family_scope
from .contacts import drop_unchecked_contacts
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
    NICKNAME_MAX_CHARS,
    THIRD_PARTY_NOTICE,
    check_input_length,
    medical_reply,
    CHILD_PLACEHOLDER,
    nickname_allowed,
    nickname_of,
    unmask_aliases,
    with_nickname,
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
    answer_view,
    ledger_docs,
    ledger_search_enabled,
    read_photo,
    search_docs,
    search_with_ledger,
)
from .packing import (
    PACKING_FAILED_TEXT,
    packing_enabled,
    setting_key,
    shiori_docs,
    suggest_packing,
)
from .presets import (
    ERRAND_TIME_ORDER,
    OWN_HOME_NAME,
    load_family_from_preset,
    new_own_family,
    profile_from_choices,
    spaced_records,
    trial_profile_from_choices,
)
from .retell import MissingKey, make_client, retell
from .week import calendar_days, doctor_view, week_view
from .work_first import (
    WorkDraftEdit,
    check_parent_edit,
    is_work_first_target,
    work_first,
    work_first_enabled,
)
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
    load_planner,
    save_planner,
    remove_trash,
    replace_trash,
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
# 夫の3択（2026-11-07 UX Design）：物を持っていく用事は「持っていく」、ほか（迎え・まとめたお願い）は
# 「引き受ける」。3つ目は「むずかしい」。古い語は前に送った返事と端末の記憶のため受け口に残す。
FAMILY_ANSWERS = {
    "持っていく": Status.ACCEPTED,
    "引き受ける": Status.ACCEPTED,
    "手配する": Status.ACCEPTED,
    "むずかしい": Status.DIFFICULT,
    "今日は難しい": Status.DIFFICULT,
    "今日はむずかしい": Status.DIFFICULT,
    ANSWER_TALK: Status.ON_HOLD,
    ANSWER_WITH_TIME: Status.OK_WITH_TIME,
}


Sex = Literal["男の子", "女の子", "答えない"]
ChildSchool = Literal["保育園・幼稚園", "学校", "なし"]
ErrandTime = Literal["平日の朝", "平日の日中", "平日の夜", "休みの日"]
SIBLINGS_MAX = 20


class Sibling(_Strict):
    """家にいるきょうだい1人。名前の欄は持たない（選ぶ形だけ）。"""

    age: StrictInt | None = Field(default=None, ge=0, le=18)
    sex: Sex | None = None
    school: ChildSchool | None = None


class SetupChoices(_Strict):
    """自分の家庭の設定。全部選ぶ形で、自由な文字を受けない（呼び名を伏せる仕組みが効かないため）。

    値は台帳に入る語で、画面の札（会社員・公務員 など）とは別。札だけ変えても値は変えない。
    """

    parent: Literal["母", "父", "その他"] | None = None
    # 画面から外した問い（一緒に暮らす大人・家でお世話が必要な子）。古い台帳の choices に答えが残り、
    # 直すときに画面がそのまま送り返すので、受け付けは残す。新しい家庭には書かない（choices_dict）。
    adult: Literal["いる", "いない"] | None = None
    child: Literal["いる", "いない"] | None = None
    # 2026-11-08 PO：きょうだいの人数と1人ずつの答え（siblings）に置き換えた。古い台帳のために受け付けは残す。
    school: Literal["保育園", "学校", "なし"] | None = None
    work: Literal["会社員", "自営業", "家のことが中心", "その他"] | None = None
    errand: Literal["夫・パートナー", "妻・パートナー", "祖父母", "いない"] | None = None
    # 2026-11-08 PO：いくつでも選ぶ時間帯（errand_times）に置き換えた。古い台帳のために受け付けは残す。
    errand_time: Literal["平日の夜と朝", "休みの日だけ", "日中も動ける"] | None = None
    bedside: Literal["いる", "いない"] | None = None
    # 2026-11-08 PO：画面から外した。古い台帳と試す家庭のために受け付けは残す（新しい設定は空で送る）。
    supports: list[Literal["延長保育", "ファミサポ", "一時預かり"]] = Field(default_factory=list)
    no_support: bool = False
    # 連絡する相手。None は選んでいない（誰も外さない）。外した相手の下書きはコードで落とす。
    contacts: list[Literal["夫", "保育園", "職場"]] | None = None
    # 入院している子（2026-11-08 PO）。どれも任意で、選ぶ形だけ。
    child_age: StrictInt | None = Field(default=None, ge=0, le=18)
    child_sex: Sex | None = None
    child_school: ChildSchool | None = None
    # 家にいるきょうだい（人数ぶんの行）と、頼める人が動ける時間帯（いくつでも）。
    siblings: list[Sibling] = Field(default_factory=list, max_length=SIBLINGS_MAX)
    errand_times: list[ErrandTime] = Field(default_factory=list, max_length=4)


# 画面から外した問い。答えが無ければ choices に書かない。
RETIRED_CHOICES = ("adult", "child", "school", "errand_time")
# 2026-11-08 に足した問い。送られなかったとき（前の画面）は choices に書かず、古い答えで profile を作る。
NEW_CHOICES = ("child_age", "child_sex", "child_school", "siblings", "errand_times")


def choices_dict(choices: SetupChoices) -> dict[str, Any]:
    """台帳の choices に入れる形。外した問いは、古い台帳の答えだけを残す。"""
    data = choices.model_dump()
    for key in RETIRED_CHOICES:
        if data.get(key) is None:
            data.pop(key, None)
    for key in NEW_CHOICES:
        if key not in choices.model_fields_set:
            data.pop(key, None)
    if "errand_times" in data:
        # 同じ時間帯を2度送っても1つ。並びは画面の並び。
        picked = set(data["errand_times"])
        data["errand_times"] = [slot for slot in ERRAND_TIME_ORDER if slot in picked]
    return data


class NewBoard(_Strict):
    home: Literal["home_a", "home_b", "shoot_a", "shoot_b", "own"]
    choices: SetupChoices | None = None
    # 子のあだ名と入院した日（①の先頭）。choices には入れない（choices は profile ごとAIへ渡る）。
    # あだ名は呼び名として持ち、AIへ送る前に［子］へ伏せる。入院した日はAIに渡さない。
    nickname: str | None = Field(default=None, max_length=NICKNAME_MAX_CHARS)
    admitted_on: date | None = None

    @field_validator("nickname")
    @classmethod
    def _nickname(cls, value: str | None) -> str | None:
        if value is not None and not nickname_allowed(value):
            raise ValueError(NICKNAME_REFUSED)
        return value


PLANNER_KEYS = {"tasks", "checklist"}
PLANNER_MAX_BYTES = 64 * 1024


def _has_quote(value: Any) -> bool:
    """持ち物の控えに、引用の欄（quote・quotes）が紛れていないか。"""
    if isinstance(value, dict):
        return any(key in ("quote", "quotes") or _has_quote(v) for key, v in value.items())
    if isinstance(value, list):
        return any(_has_quote(v) for v in value)
    return False


class PlannerBody(_Strict):
    """持ち物と知らせ（端末の控えと同じ形）。台帳の外に置き、AIへは渡さない。大きさに上限を持つ。"""

    planner: dict[str, Any]

    @field_validator("planner")
    @classmethod
    def _small_and_known(cls, value: dict[str, Any]) -> dict[str, Any]:
        unknown = set(value) - PLANNER_KEYS
        if unknown:
            raise ValueError(f"知らない欄です: {sorted(unknown)}")
        if any(not isinstance(part, dict) for part in value.values()):
            raise ValueError("欄の中身は形のあるものだけです")
        if _has_quote(value.get("checklist")):
            # しおりの引用は端末の中だけ（11/8 Plan main の案(a)）。サーバーの控えには入れない。
            raise ValueError("しおりの引用はサーバーに残しません")
        size = len(json.dumps(value, ensure_ascii=False).encode("utf-8"))
        if size > PLANNER_MAX_BYTES:
            raise ValueError("大きすぎます")
        return value


class SetupRequest(_Strict):
    version: int
    choices: SetupChoices
    # 送らなければ今のまま（空の文字・null を送ると消す）。
    nickname: str | None = Field(default=None, max_length=NICKNAME_MAX_CHARS)
    admitted_on: date | None = None

    @field_validator("nickname")
    @classmethod
    def _nickname(cls, value: str | None) -> str | None:
        if value is not None and not nickname_allowed(value):
            raise ValueError(NICKNAME_REFUSED)
        return value


NOT_FOR_THIS_ITEM = "この項目には使えません"
NICKNAME_REFUSED = "このあだ名は使えません"
# 設定を直せる試す家庭の名前（プリセットの profile.name）。
TRIAL_HOME_NAMES = {"家庭A", "家庭B"}
# 返事待ちの連絡をもう一度共有した記録の語。返事ではないので、AIの経緯にも返事の判定にも数えない。
RESHARED_LABEL = "もう一度共有した"


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
    # 親の画面だけが true を送る（夫がリンクから開いた画面では、やりとりを探さない）。
    # 切り替え（reading.LEDGER_SEARCH）が off なら見ない。
    ledger: bool = False


class PackingRequest(_Strict):
    """持ち物の案を作るときに、端末が持っている読み取り済みのしおりの文を送る。保存しない。"""

    docs: list[Doc] = Field(default_factory=list)


class SearchRequest(_Strict):
    """端末が持っている読み取り済みの文。保存しない。"""

    docs: list[Doc]


NOTE_MAX_CHARS = 200
ONE_NOTE_PREFIX = "n-"  # 夫のひとことの項目の印。案から作った連絡の s- と同じ考え


class FamilyReply(_Strict):
    """家族の1件の答え。版は受け取らない（親の画面の更新と競わせない）。"""

    answer: Literal[
        "持っていく",
        "引き受ける",
        "手配する",
        "むずかしい",
        "今日は難しい",
        "今日はむずかしい",
        "時間を相談したい",
        "◯時なら",
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


# 前のあだ名の伏せ字。あだ名を変える・消すときに、前のあだ名をこの印で残す（QA Test の P1-8）。
FORMER_CHILD_MARKS = tuple(f"［子の前の呼び名{letter}］" for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ")


def _swap_marks(value: Any, old: str, new: str) -> Any:
    """文字の欄の伏せ字を付け替える（dict と list の中も）。"""
    if isinstance(value, str):
        return value.replace(old, new)
    if isinstance(value, list):
        return [_swap_marks(entry, old, new) for entry in value]
    if isinstance(value, dict):
        return {key: _swap_marks(entry, old, new) for key, entry in value.items()}
    return value


def _swap_family_marks(
    family: Family, trash_entries: list[tuple[str, dict]], old: str, new: str
) -> list[tuple[str, dict]]:
    """台帳の文（項目と分かれ道）とゴミ箱の控えの伏せ字 old を new に付け替える。書き換えた控えを返す。"""
    swap = lambda text: text.replace(old, new) if text else text
    for item in family.items:
        for field in type(item).model_fields:
            value = getattr(item, field)
            if isinstance(value, str) and old in value:
                setattr(item, field, swap(value))
    family.branches = [map_branch_text(branch, swap) for branch in family.branches]
    return [
        (trash_id, _swap_marks(entry, old, new))
        for trash_id, entry in trash_entries
        if old in json.dumps(entry, ensure_ascii=False)
    ]


def change_child_nickname(
    family: Family, nickname: str | None, trash_entries: list[tuple[str, dict]]
) -> dict[str, dict]:
    """子のあだ名を変える・消す（QA Test の P1-8・P1-9）。書き換えたゴミ箱の控えを返す。

    今のあだ名は「前の呼び名」の伏せ字へ移す。台帳の文は伏せ字のまま残るので、医師の言葉の原文は表示で
    元に戻り、前のあだ名は AI へ渡らない（本名を入れて消した場合も）。前に使ったあだ名へ戻すときは、
    その伏せ字を［子］へ戻す。ゴミ箱の控えも同じに付け替える。
    """
    changed: dict[str, dict] = {}
    entries = list(trash_entries)

    def apply(old: str, new: str) -> None:
        nonlocal entries
        for trash_id, entry in _swap_family_marks(family, entries, old, new):
            changed[trash_id] = entry
        entries = [(trash_id, changed.get(trash_id, entry)) for trash_id, entry in entries]

    current = nickname_of(family.aliases)
    name = (nickname or "").strip()
    if current == (name or None):
        return {}
    aliases = dict(family.aliases)
    if current:
        mark = next(entry for entry in FORMER_CHILD_MARKS if entry not in aliases.values())
        apply(CHILD_PLACEHOLDER, mark)
        aliases[current] = mark
    if name:
        former = aliases.get(name)
        if former in FORMER_CHILD_MARKS:
            apply(former, CHILD_PLACEHOLDER)
        aliases[name] = CHILD_PLACEHOLDER
    family.aliases = aliases
    return changed


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
    """新しい案ができた後も、まだ送っていない前の案の連絡。状態は変えず、親が決める。"""
    earlier = {
        sent_item_id(branch.id, o, a)
        for branch in family.branches[:-1]
        for o, outcome in enumerate(branch.outcomes)
        for a in range(len(outcome.actions))
    }
    return {item.id for item in family.items if item.id in earlier and item.status == Status.TO_ASK}


def carried_item_ids(family: Family) -> dict[str, str]:
    """案の文に引き継がれた、まだ頼んでいない用事と、引き継いだ送る文のID。

    図の⑦：夫への文は1通で、親の「着替えも頼みたい」や夫の「今夜、何を持っていく？」への答えは
    案の文に入っている。同じ相手への文が根拠に挙げたものだけ。引き継いだ送る文が台帳から消えたら
    （親が下書きを消したら）、用事は元の頼みごとに戻る（QA Test の P1-1）。状態は変えない。
    """
    by_id = {item.id: item for item in family.items}
    carried: dict[str, str] = {}
    for branch in family.branches:
        for o, outcome in enumerate(branch.outcomes):
            for a, action in enumerate(outcome.actions):
                sent = sent_item_id(branch.id, o, a)
                if action.type == ActionType.NOTHING or not action.draft or sent not in by_id:
                    continue
                for basis_id in action.basis_ids:
                    item = by_id.get(basis_id)
                    if (
                        item is not None
                        and item.part == action.part
                        and item.kind == Kind.ACTION
                        and item.status == Status.TO_ASK
                        and not is_from_proposal(item)
                    ):
                        carried[item.id] = sent
    return carried


def board_view(
    family: Family, events: dict[str, list[dict[str, str]]] | None = None
) -> dict[str, Any]:
    """画面へ渡す形。家族コードは返さない。表示名と固定文はサーバーの1か所で決める。"""
    data = family.model_dump(mode="json", exclude={"code"})
    events = events or {}
    released = released_items(family)
    outdated = outdated_item_ids(family)
    superseded = superseded_item_ids(family)
    carried = carried_item_ids(family)
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
        # 案の文に引き継がれた用事。画面は済の扱いにし、送る一覧と未送信の数に入れない。
        shown["carried_into"] = carried.get(item.id)
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
    # 子のあだ名（呼び名の［子］）。「いま」の1行と夫の画面に出す。入院した日は admitted_on のまま。
    data["nickname"] = nickname_of(family.aliases)
    # ④の「質問する」がやりとりも探すか（切り替えは reading.py の1か所）。画面の語の出し分けに使う。
    data["ledger_search"] = ledger_search_enabled()
    # 職場あての親の頼みごとに［送る文を見る］を出すか（切り替えは work_first.py の1か所）。
    data["work_first"] = work_first_enabled()
    # 持ち物でAIの案を作るか（切り替えは packing.py の1か所）と、設定の指紋（「設定が変わりました」の判定）。
    data["packing_ai"] = packing_enabled()
    data["packing_setting"] = setting_key(family.profile)
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
    work_calls = OnceCache(REMEMBERED_PROPOSALS)
    packing_calls = OnceCache(REMEMBERED_PROPOSALS)

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
        data = board_view(family, list_events(engine, family.code))
        # 持ち物と知らせ（台帳の外の表、2026-11-08 PO）。画面の控えと揃えるため毎回付ける。AIへは渡さない。
        data["planner"] = load_planner(engine, family.code)
        return data

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
            own = new_own_family(choices_dict(body.choices) if body.choices else None)
            own.aliases = with_nickname(own.aliases, body.nickname)
            own.admitted_on = body.admitted_on
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
        """家庭の設定を直す。次のAIの呼び出しから効く。

        試す家庭（家庭A・B）も、そのボードだけ直せる（2026-11-07 PO）。プリセットのファイルは変えないので、
        ［家庭A/Bで試す］は毎回もとの設定から始まり、eval の家庭A・Bも変わらない。名前は元のまま残す。
        """
        family = read(x_family_code)
        require_version(family, body.version)
        name = family.profile.name
        if name != OWN_HOME_NAME and name not in TRIAL_HOME_NAMES:
            raise HTTPException(status_code=422, detail=NOT_FOR_THIS_ITEM)
        choices = choices_dict(body.choices)
        if name == OWN_HOME_NAME:
            family.profile = profile_from_choices(choices)
        else:
            # 試す家庭は、変えた欄だけ置き換える（何も変えなければ触らない。QA Test の P1）。
            family.profile = trial_profile_from_choices(family.profile, choices)
        swapped_trash: dict[str, dict] = {}
        if "nickname" in body.model_fields_set:
            swapped_trash = change_child_nickname(
                family, body.nickname, list_trash(engine, family.code)
            )
        if "admitted_on" in body.model_fields_set:
            family.admitted_on = body.admitted_on
        saved = save(family, body.version)
        for trash_id, entry in swapped_trash.items():
            replace_trash(engine, family.code, trash_id, entry)
        return saved

    @router.get("/board")
    def get_board(x_family_code: str | None = Header(default=None)) -> dict[str, Any]:
        return view(read(x_family_code))

    @router.put("/board/planner")
    def put_planner(
        body: PlannerBody, x_family_code: str | None = Header(default=None)
    ) -> dict[str, Any]:
        """持ち物と知らせを丸ごと置き換える。台帳の版は進めない（台帳とは別の表）。"""
        family = read(x_family_code)
        save_planner(engine, family.code, body.planner)
        return {"planner": body.planner}

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
                    # 画面は結果のときだけ自動で案を始める（図の③：医師の言葉の直後は予定と次にできること）。
                    "stage": trigger[1] if trigger else None,
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
            # 連絡する相手のチェックを外した相手への案は、案にも線の図にも出さない。
            branch = (
                drop_unchecked_contacts(result.branch, family.profile) if result.branch else None
            )
            return {
                "item_id": item.id,
                "stage": stage,
                "branch": branch_view(branch, family.aliases) if branch else None,
                "message": result.message,
                # 押す前の案を週の表と線で見せる。台帳には書かない。
                "week": week_view(family, today_jst(), branch) if branch else None,
                "impact": preview_impact(family, branch) if branch else None,
            }

        # 守りで案を出せなかった結果は覚えない。［もう一度］がAIを呼び直せるように。
        return proposal_calls.run(
            (family_scope(family.code), item.id, family.version, chosen),
            propose,
            keep=lambda result: result["branch"] is not None,
        )

    def work_target(family: Family, item_id: str) -> Item:
        """職場への文を作れる項目。切り替えが off なら、どの項目にも使えない。"""
        item = next((entry for entry in family.items if entry.id == item_id), None)
        if item is None:
            raise HTTPException(status_code=404, detail=ITEM_NOT_FOUND)
        if not work_first_enabled() or not is_work_first_target(item, family.profile):
            raise HTTPException(status_code=422, detail=NOT_FOR_THIS_ITEM)
        return item

    @router.post("/board/items/{item_id}/work-first")
    def make_work_draft(
        item_id: str, x_family_code: str | None = Header(default=None)
    ) -> dict[str, Any]:
        """職場への文を作って返すだけ。台帳には書かない（文は端末が持つ。v3.5.11）。

        状態は親が［送った］を押したときだけ変わる（今までの状態の変更を使う）。
        同じ版の台帳への同じ要求は、送り直しなのでAIを呼び直さない。
        """
        family = read(x_family_code)
        item = work_target(family, item_id)

        def make() -> dict[str, Any]:
            try:
                result = work_first(
                    client_factory(),
                    family,
                    item.id,
                    before_call=lambda: consume_family_call(engine, family.code),
                    today=today_jst(),
                )
            except DailyLimitReached:
                raise HTTPException(status_code=429, detail=AI_LIMIT_REACHED) from None
            except (MissingKey, anthropic.APIError):
                raise HTTPException(status_code=503, detail=CANNOT_PROPOSE_TEXT) from None
            draft = unmask_aliases(result.draft, family.aliases) if result.draft else None
            return {"item_id": item.id, "draft": draft, "message": result.message}

        return work_calls.run(
            (family_scope(family.code), item.id, family.version),
            make,
            keep=lambda result: result["draft"] is not None,
        )

    @router.post("/board/items/{item_id}/work-first/edited")
    def check_work_edit(
        item_id: str, body: WorkDraftEdit, x_family_code: str | None = Header(default=None)
    ) -> dict[str, Any]:
        """親が直した職場への文を確かめて返す。医療の語だけ当てる。保存しない。AIは呼ばない。"""
        family = read(x_family_code)
        item = work_target(family, item_id)
        text = body.text.strip()
        if not text:
            raise HTTPException(status_code=422, detail=NOT_FOR_THIS_ITEM)
        if check_parent_edit(mask_aliases(text, family.aliases)):
            raise HTTPException(status_code=422, detail=CANNOT_SEND_MEDICAL_TEXT)
        return {"item_id": item.id, "draft": text}

    @router.post("/board/packing")
    def make_packing(
        body: PackingRequest, x_family_code: str | None = Header(default=None)
    ) -> dict[str, Any]:
        """持ち物の案を作って返すだけ（v3.6.1）。台帳にも持ち物の控えにも書かない（親が押した物だけ端末が足す）。

        AIへは設定の選ぶ形の答えと、しおりの文と、型だけを渡す。控え（親が足した・直した・消した物）は
        AIへ渡さず、出てきた案から外すのに使う。切り替えが off なら、AIを呼ばずに型のまま（message は無し）。
        AIが使えない・上限・守りで2回落ちたときは、案なしで「案を作れませんでした。型のまま使えます」。
        """
        family = read(x_family_code)
        if not packing_enabled():
            return {"suggestions": [], "fell_back": True, "message": None, "enabled": False}
        docs: list[Doc] = []
        total = 0
        for doc in shiori_docs(body.docs)[:MAX_DOCS]:
            if total + len(doc.text) > MAX_DOCS_TOTAL_CHARS:
                break
            total += len(doc.text)
            docs.append(doc)
        checklist = load_planner(engine, family.code).get("checklist")
        failed = {
            "suggestions": [],
            "fell_back": True,
            "message": PACKING_FAILED_TEXT,
            "enabled": True,
        }

        def make() -> dict[str, Any]:
            try:
                result = suggest_packing(
                    client_factory(),
                    family.profile,
                    docs,
                    aliases=family.aliases,
                    checklist=checklist,
                    family_code=family.code,
                    before_call=lambda: consume_family_call(engine, family.code),
                )
            except (DailyLimitReached, MissingKey, anthropic.APIError):
                return failed
            if result.suggestions is None:
                return failed
            suggestions = [
                {
                    **entry,
                    "quote": (
                        unmask_aliases(entry["quote"], family.aliases)
                        if entry.get("quote")
                        else None
                    ),
                }
                for entry in result.suggestions
            ]
            return {
                "suggestions": suggestions,
                "fell_back": False,
                "message": None,
                "enabled": True,
            }

        # 同じ設定・同じしおり・同じ控えへの送り直しは、AIを呼び直さない（画面の二重の呼び出しも）。
        key = (
            family_scope(family.code),
            setting_key(family.profile),
            json.dumps([doc.model_dump() for doc in docs], ensure_ascii=False),
            json.dumps(checklist, ensure_ascii=False, sort_keys=True),
        )
        return packing_calls.run(key, make, keep=lambda result: not result["fell_back"])

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
        # 端末の控えは設定を変える前のものもある。外した相手への案は保存しない。
        branch = drop_unchecked_contacts(branch, family.profile)
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

    @router.post("/board/items/{item_id}/reshare")
    def reshare_item(
        item_id: str, body: UndoRequest, x_family_code: str | None = Header(default=None)
    ) -> dict[str, Any]:
        """親が返事待ちの連絡を、同じ文・同じリンクでもう一度共有した記録（2026-11-07 PO）。

        状態は返事待ちのまま。経緯に時刻だけ残す。自動の通知・回数・催促の文は作らない。AIは呼ばない。
        """
        family = read(x_family_code)
        require_version(family, body.version)
        item = next((entry for entry in family.items if entry.id == item_id), None)
        if item is None:
            raise HTTPException(status_code=404, detail=ITEM_NOT_FOUND)
        released = released_items(family)
        if not item.id.startswith("s-") or item.status != Status.ASKED or item.id in released:
            raise HTTPException(status_code=422, detail=NOT_FOR_THIS_ITEM)
        return save(family, body.version, [ItemEvent(item_id, now_jst(), RESHARED_LABEL)])

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
        # 語に当たる問いは、AIを呼ばずに固定文だけを返す。「見つかりません」と言わない。
        # 資料が0枚のときも先に見る（④の検索欄は資料が無くても出る）。
        if find_medical_words(body.question) or find_symptom_words(body.question):
            return {"medical": MEDICAL_FIXED_TEXT, "found": False}
        if ledger_search_enabled() and body.ledger:
            return search_with_the_ledger(family, body)
        if (
            not body.docs
            or len(body.docs) > MAX_DOCS
            or sum(len(doc.text) for doc in body.docs) > MAX_DOCS_TOTAL_CHARS
            or any(doc.category not in READABLE_CATEGORIES for doc in body.docs)
        ):
            raise HTTPException(status_code=422, detail=NOT_SEARCHABLE)
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

    def search_with_the_ledger(family: Family, body: FreeSearchRequest) -> dict[str, Any]:
        """④をやりとりへ広げた道（切り替えが on で、親の画面から）。問いは台帳に入れない。

        答えは項目か資料の文の引用だけで、出どころ（誰の言葉か・入れた時刻・要約か）を添える。
        取り込みと同じ仕分けを保存せずに通し、医療の問いなら固定文だけを返して探さない。
        病院のことかどうかは、仕分けの相手（part）でコードが決める。そのため、ここだけは
        相手を「病院」に決めずに仕分ける（今までの道は相手を病院に決めて仕分けている）。
        """
        if (
            len(body.docs) > MAX_DOCS
            or sum(len(doc.text) for doc in body.docs) > MAX_DOCS_TOTAL_CHARS
            or any(doc.category not in READABLE_CATEGORIES for doc in body.docs)
            or (not body.docs and not ledger_docs(family))
        ):
            raise HTTPException(status_code=422, detail=NOT_SEARCHABLE)
        count_call = lambda: consume_family_call(engine, family.code)
        try:
            sorted_result = intake(
                client_factory(),
                family,
                body.question,
                now_jst(),
                new_item_id,
                before_call=count_call,
                choices=Choices(said="聞きたい"),
            )
            if any(item.medical == Medical.ASK for item in sorted_result.items):
                return {"medical": MEDICAL_FIXED_TEXT, "found": False}
            if sorted_result.message:
                raise HTTPException(status_code=422, detail=sorted_result.message)
            result = search_with_ledger(
                client_factory(), family, body.question, body.docs, before_call=count_call
            )
        except DailyLimitReached:
            raise HTTPException(status_code=429, detail=AI_LIMIT_REACHED) from None
        except (MissingKey, anthropic.APIError):
            raise HTTPException(status_code=503, detail=NOT_SEARCHABLE) from None
        return {
            "medical": None,
            "found": bool(result.answers),
            "answers": [answer_view(answer, family.aliases) for answer in result.answers],
            # 見つからないときに［病院に聞くことに残す］を出すか。病院のことだけ。
            "hospital": any(item.part == Part.HOSPITAL for item in sorted_result.items),
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
