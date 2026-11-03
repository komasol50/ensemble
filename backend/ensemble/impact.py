"""影響マップ。台帳の最新の案から、相手ごとの「変える／外す／そのまま」を作る。AIを呼ばない。

データの無い線を描かないよう、項目か案がある相手だけを返す。関係しない用件は変えない。
"""

from typing import Any

from .guards import unmask_aliases
from .ledger import ActionType, Family, Item, Part, Status, sent_item_id, status_label

PART_ORDER = (Part.FAMILY, Part.WORK, Part.CHILDCARE, Part.SUPPORT, Part.HOSPITAL)
CHANGE = "変える"
RELEASE = "外す"
KEEP = "そのまま"
FINISHED = (Status.DELIVERED, Status.DONE, Status.ANSWERED)


def _short(item: Item) -> str:
    return item.task_summary or item.text_original or ""


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
        before = released or (existing_open[:2] if changes else [])
        basis_ids = dict.fromkeys(i for _, _, action in changes for i in action.basis_ids)
        kept = (
            [{"text": _short(item), "state": _state(item)} for item in existing_open]
            if effects == [KEEP]
            else []
        )
        if released:
            summary = f"{RELEASE}：{_short(released[0])}"
        elif changes:
            summary = changes[0][2].draft or ""
        elif kept:
            first = kept[0]
            summary = f"{first['text']}：{first['state']}" if first["state"] else first["text"]
        else:
            summary = "変更なし"
        nodes.append(
            {
                "part": part.value,
                "effects": effects,
                "summary": summary,
                "before": [_short(item) for item in before],
                "after": after,
                "basis": [_quote(by_id[i]) for i in basis_ids if i in by_id and i not in made_ids],
                "kept": kept,
            }
        )

    cancelled = [day.day for day in branch.candidate_days if day.cancelled]
    if cancelled:
        headline = f"{'・'.join(cancelled)}の退院候補を取り消し"
    else:
        headline = "／".join(outcome.label for outcome in branch.outcomes)

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

    return show(
        {
            "headline": headline,
            "basis": [_quote(item) for item in sources],
            "nodes": nodes,
        }
    )
