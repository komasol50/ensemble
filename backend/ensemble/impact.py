"""影響マップ。台帳の最新の案から、相手ごとの「変える／外す／そのまま」を作る。AIを呼ばない。

データの無い線を描かないよう、項目か案がある相手だけを返す。関係しない用件は変えない。
"""

import re
from datetime import datetime
from typing import Any

from .budget import JST
from .guards import PARENT_SPEAKERS, unmask_aliases
from .week import next_rows
from .ledger import ActionType, Family, Item, Part, Status, sent_item_id, status_label

PART_ORDER = (Part.FAMILY, Part.WORK, Part.CHILDCARE, Part.SUPPORT, Part.HOSPITAL)
CHANGE = "変える"
RELEASE = "外す"
KEEP = "そのまま"
FINISHED = (Status.DELIVERED, Status.DONE, Status.ANSWERED)
TASK_VERBS = {
    ActionType.REQUEST: "に頼む",
    ActionType.TELL: "に伝える",
    ActionType.CONFIRM: "に確認する",
    ActionType.CHECK_AVAILABILITY: "に空きを確認する",
}
NO_CHANGE = "変更なし"
CANCEL_END = " → 取り消し"  # 画面はこの語尾を切らずに出す（words.ts の map.changeEnd）
RECORDED_HEADLINE = "退院の候補日を記録しました"


def _who(part: Part, family: Family) -> str:
    """やることの宛先の呼び方。"""
    if part == Part.FAMILY and family.profile.errand_people:
        return family.profile.errand_people[0].label
    return {Part.CHILDCARE: "保育園"}.get(part, part.value)


def _task(part: Part, types: list[ActionType], released: bool, family: Family) -> str:
    """利用者がすることを一文にする。AIの文ではなく、案の種類からコードで作る。"""
    who = _who(part, family)
    tasks = [f"{who}{TASK_VERBS[kind]}" for kind in dict.fromkeys(types) if kind in TASK_VERBS]
    if tasks:
        return "、".join(tasks)
    return "前の確認を取り消す" if released else NO_CHANGE


_SENTENCE = re.compile(r"[^。！？!?\n]+[。！？!?]?")
_GREETING = re.compile(
    r"^(いつも)?(お世話|おせわ|お疲れ|おつかれ|こんにち|こんばん|おはよう|ごめん|すみません|失礼します|ありがとう)"
)
_GREETING_MAX_CHARS = 20


def _first_sentence(draft: str) -> str:
    """下書きの最初の用件の文。挨拶は飛ばす。文を作らず、原文のまま切り出す。"""
    sentences = [part.strip() for part in _SENTENCE.findall(draft) if part.strip()]
    for sentence in sentences:
        # 「すみません、木曜の…」のように用件を含む長い文は、挨拶として飛ばさない。
        if not (_GREETING.match(sentence) and len(sentence) <= _GREETING_MAX_CHARS):
            return sentence
    return sentences[0] if sentences else draft


def _change_line(released: list[Item], drafts: list[str]) -> str | None:
    """カードの前→後の1行。取り消す項目があればその項目、無ければ新しい連絡の用件。"""
    if released:
        return "／".join(short_text(item) for item in released) + CANCEL_END
    if drafts:
        return f"（新規）{_first_sentence(drafts[0])}"
    return None


def short_text(item: Item) -> str:
    """項目を短く言う。送った連絡は全文が入っているので、最初の用件の文だけにする。文は作らない。"""
    if item.task_summary:
        return item.task_summary
    return _first_sentence(item.text_original or "").rstrip("。")


def _quote(item: Item) -> dict[str, str]:
    speaker = "あなた" if item.speaker in ("親", "母", "父", "私", "自分") else item.speaker
    return {"speaker": speaker or "", "text": item.text_original or item.task_summary or ""}


def _state(item: Item) -> str | None:
    return status_label(item.status, item.due, item.part) if item.status else None


def impact_view(family: Family) -> dict[str, Any] | None:
    """画面へ渡す影響マップ。進めた案が無ければ None。"""
    if not family.branches:
        return None
    branch = family.branches[-1]
    by_id = {item.id: item for item in family.items}
    placed = [
        (o, a, action)
        for o, outcome in enumerate(branch.outcomes)
        for a, action in enumerate(outcome.actions)
    ]

    nodes: list[dict[str, Any]] = []
    for part in PART_ORDER:
        here = [item for item in family.items if item.part == part]
        ours = [entry for entry in placed if entry[2].part == part]
        changes = [
            entry for entry in ours if entry[2].type != ActionType.NOTHING and entry[2].draft
        ]
        released = [
            by_id[item_id]
            for item_id in dict.fromkeys(i for _, _, action in ours for i in action.release)
            if item_id in by_id
        ]
        if not here and not changes and not released:
            continue  # データの無い線は描かない

        effects = [RELEASE] if released else []
        if changes:
            effects.append(CHANGE)
        if not effects:
            effects = [KEEP]

        made_ids = {sent_item_id(branch.id, o, a) for o, a, _ in ours}
        existing_open = [
            item for item in here if item.id not in made_ids and item.status not in FINISHED
        ]
        after = []
        for o, a, action in changes:
            made = by_id.get(sent_item_id(branch.id, o, a))
            after.append({"text": action.draft, "state": _state(made) if made else None})
        # 取り消すものだけを出す。残りの用件を「取り消す」と見せない。
        before = released
        basis_ids = dict.fromkeys(i for _, _, action in changes for i in action.basis_ids)
        kept = (
            [{"text": short_text(item), "state": _state(item)} for item in existing_open]
            if effects == [KEEP]
            else []
        )
        nodes.append(
            {
                "part": part.value,
                "who": (
                    f"家族（{_who(part, family)}）"
                    if part == Part.FAMILY and family.profile.errand_people
                    else part.value
                ),
                "effects": effects,
                "task": _task(part, [entry[2].type for entry in changes], bool(released), family),
                # 頼みごとの引用があれば、1文目の状況の文でなくそれを出す。
                "change": _change_line(
                    released,
                    [entry[2].ask or entry[2].draft for entry in changes if entry[2].draft],
                ),
                "before": [short_text(item) for item in before],
                "after": after,
                "basis": [_quote(by_id[i]) for i in basis_ids if i in by_id and i not in made_ids],
                "kept": kept,
            }
        )

    cancelled = [day.day for day in branch.candidate_days if day.cancelled]
    # 見出しはコードの固定文。AIの見出しをそのまま出さない。
    if cancelled:
        headline = f"{'・'.join(cancelled)}の退院候補を取り消し"
    else:
        headline = RECORDED_HEADLINE

    cited = {basis for _, _, action in placed for basis in action.basis_ids}
    condition = by_id.get(branch.condition_item_id)
    sources = [condition] if condition else []
    sources += [
        item
        for item in family.items
        if item.id in cited and item.refers_to == branch.condition_item_id and item.text_original
    ]

    def show(value: Any) -> Any:
        if isinstance(value, str):
            return unmask_aliases(value, family.aliases)
        if isinstance(value, list):
            return [show(entry) for entry in value]
        if isinstance(value, dict):
            return {key: show(entry) for key, entry in value.items()}
        return value

    # 原因は親が入れた結果の文だけ。医師の言葉を「あなたの入力」と出さない。
    said_by_parent = [
        item
        for item in family.items
        if item.text_original
        and (item.speaker in PARENT_SPEAKERS or item.speaker is None)
        and (
            item.refers_to == branch.condition_item_id
            or (item.id == branch.condition_item_id and item.refers_to)
        )
    ]
    return show(
        {
            "cause": said_by_parent[-1].text_original if said_by_parent else None,
            "headline": headline,
            "basis": [_quote(item) for item in sources],
            "nodes": nodes,
            "next": next_rows(family, branch, datetime.now(JST).date()),
            "candidates": [
                {
                    "day": day.day,
                    "certainty": str(day.certainty.value),
                    "cancelled": day.cancelled,
                }
                for day in branch.candidate_days
            ],
        }
    )
