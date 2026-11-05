"""条件つきの台帳の型。スキーマは契約で、欄を足さない。

列挙の値は画面と同じ日本語にする。表示名への読み替えは status_label に集める。
"""

import secrets
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

FAMILY_CODE_BYTES = 32  # 128ビット以上の乱数（256ビット）


class Kind(StrEnum):
    INFO = "情報"
    QUESTION = "質問"
    ACTION = "行動"
    EVENT = "予定"


class Part(StrEnum):
    HOSPITAL = "病院"
    FAMILY = "家族"
    CHILDCARE = "保育園・預け先"
    WORK = "職場"
    SUPPORT = "支援"
    PARENT = "親"


class Certainty(StrEnum):
    CONFIRMED = "確定"
    LIKELY = "見込み"
    GUESS = "推測"


class Medical(StrEnum):
    ASK = "ask"
    INFO = "info"
    NONE = "なし"


class Status(StrEnum):
    TO_ASK = "頼みたい"
    ASKED = "頼んだ"
    ACCEPTED = "引き受けた"
    DIFFICULT = "難しい"
    ON_HOLD = "保留"
    OK_WITH_TIME = "時刻つきで可"
    DELIVERED = "届いた"
    DONE = "完了"
    UNCONFIRMED = "未確認"
    ANSWERED = "答えあり"


class ActionType(StrEnum):
    CONFIRM = "確認する"
    CHECK_AVAILABILITY = "空きを確認する"
    REQUEST = "頼む"
    TELL = "伝える"
    NOTHING = "何もしない"


ACTION_STATUSES = {
    Status.TO_ASK,
    Status.ASKED,
    Status.ACCEPTED,
    Status.DIFFICULT,
    Status.ON_HOLD,
    Status.OK_WITH_TIME,
    Status.DELIVERED,
    Status.DONE,
}
QUESTION_STATUSES = {Status.UNCONFIRMED, Status.ANSWERED}

PROGRESS_GROUPS: dict[str, set[Status]] = {
    "未依頼": {Status.TO_ASK},
    "返答待ち": {Status.ASKED},
    "返事あり": {Status.ACCEPTED, Status.DIFFICULT, Status.ON_HOLD, Status.OK_WITH_TIME},
    "済": {Status.DELIVERED, Status.DONE},
}


class _Strict(BaseModel):
    """知らない欄を受け付けない（欄を足さないための守り）。"""

    model_config = ConfigDict(extra="forbid")


class Item(_Strict):
    id: str = Field(min_length=1)
    kind: Kind
    part: Part
    text_original: str | None = None  # 第三者の文は保存しない
    task_summary: str | None = None
    speaker: str | None = None  # 不明可
    recorded_at: str | None = None
    certainty: Certainty
    condition: str | None = None
    medical: Medical = Medical.NONE
    refers_to: str | None = None
    status: Status | None = None
    owner: str | None = None
    due: str | None = None
    location: str | None = None

    @model_validator(mode="after")
    def status_matches_kind(self) -> "Item":
        if self.status is None:
            return self
        if self.kind == Kind.ACTION and self.status not in ACTION_STATUSES:
            raise ValueError(f"行動に使えない状態です: {self.status}")
        if self.kind == Kind.QUESTION and self.status not in QUESTION_STATUSES:
            raise ValueError(f"質問に使えない状態です: {self.status}")
        if self.kind not in (Kind.ACTION, Kind.QUESTION):
            raise ValueError(f"{self.kind}に状態は持たせません")
        return self


class CandidateDay(_Strict):
    """退院候補日。日は医師の言葉の表現のまま持つ。取り消した日も消さない。"""

    day: str = Field(min_length=1)
    certainty: Certainty
    cancelled: bool = False


class NextKind(StrEnum):
    """分かれ道の先の日の種類。決まった3つだけ。"""

    DISCHARGE = "退院候補"
    TEST = "検査"
    OTHER = "その他"


class NextStep(_Strict):
    """分かれ道の先の日。文は書かせず、医師の言葉の引用と種類だけを持つ。"""

    day: str = Field(min_length=1)
    kind: NextKind
    clause: str = Field(min_length=1)


class Action(_Strict):
    part: Part
    type: ActionType
    draft: str | None = None
    basis_ids: list[str] = Field(default_factory=list)
    release: list[str] = Field(default_factory=list)  # 外す項目のID
    ask: str | None = None  # 下書きの中の、頼みごとの文の引用


class Outcome(_Strict):
    label: str
    actions: list[Action] = Field(default_factory=list)
    clause: str | None = None  # この先を指す医師の言葉の引用
    clause_item_id: str | None = None  # 引用のもとの項目。病院の項目だけ
    next: list[NextStep] = Field(default_factory=list)


class Branch(_Strict):
    id: str = Field(min_length=1)
    condition_item_id: str
    candidate_days: list[CandidateDay] = Field(default_factory=list)
    outcomes: list[Outcome] = Field(default_factory=list)


class ErrandPerson(_Strict):
    """用事を頼める人。"""

    label: str
    availability: str


class Support(_Strict):
    """登録している支援・預け先。"""

    name: str
    detail: str = ""


class Profile(_Strict):
    """その家の設定（プリセット）。"""

    name: str
    bedside_others: list[str] = Field(default_factory=list)  # あなたのほかに病室に泊まれる人
    errand_people: list[ErrandPerson] = Field(default_factory=list)
    older_child: str = ""
    work: str = ""
    grandparents: str = ""
    supports: list[Support] = Field(default_factory=list)


class Rule(_Strict):
    """作り手のルール。文は表の原文のまま持つ。"""

    id: str
    text: str
    active: bool


class Family(_Strict):
    code: str = Field(min_length=32)
    version: int = Field(default=1, ge=1)
    profile: Profile
    aliases: dict[str, str] = Field(default_factory=dict)
    rules: list[Rule] = Field(default_factory=list)
    items: list[Item] = Field(default_factory=list)
    branches: list[Branch] = Field(default_factory=list)


def new_family_code() -> str:
    """URLに入れる家族コードを作る。"""
    return secrets.token_urlsafe(FAMILY_CODE_BYTES)


def sent_item_id(branch_id: str, outcome: int, action: int) -> str:
    """案の連絡に対応する項目のID。台帳に欄を足さず、案の位置から決める。"""
    return f"s-{branch_id}-{outcome}-{action}"


def status_label(status: Status, due: str | None = None, part: Part | None = None) -> str:
    """状態の画面の表示名。時刻つきは、家族の答えなら「◯なら」、ほかの返事なら「◯まで可」。"""
    if status == Status.TO_ASK:
        return "未依頼"
    if status == Status.ASKED:
        return "返答待ち"
    if status == Status.OK_WITH_TIME:
        if not due:
            return "時刻つきで可"
        return f"{due}なら" if part == Part.FAMILY else f"{due}まで可"
    return status.value


def progress_group(status: Status) -> str | None:
    """進捗別の4つのまとまりのどれか。質問の状態は入らない。"""
    for name, statuses in PROGRESS_GROUPS.items():
        if status in statuses:
            return name
    return None
