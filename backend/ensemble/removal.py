"""項目を消すことと、ゴミ箱から戻すこと。

誤りは「消す」で直すので、案の根拠の項目も消せる。消すと、それを根拠にした案も取り下げる。
戻せるように、変えたものの控えを返す。台帳に欄を足さないよう、控えはゴミ箱の表に置く。
"""

from typing import Any

from .ledger import Action, ActionType, Branch, Family, Item, Status, sent_item_id


def withdraw_item(item_id: str, family: Family) -> dict[str, Any]:
    """項目を消し、それを根拠にした案も取り下げる。戻すための控えを返す。

    条件の項目が消えたら分かれ道ごと、根拠が消えた案はその案だけを取り下げる。
    案の位置で項目のIDを決めているので、取り下げた案は抜かずに「何もしない」にする。
    送っていない連絡の項目は一緒に消し、送った連絡の項目は事実として残す。
    """
    index, target = next(
        (position, item) for position, item in enumerate(family.items) if item.id == item_id
    )
    undo: dict[str, Any] = {
        "item": target.model_dump(mode="json"),
        "index": index,
        "also_removed": [],
        "actions": [],
        "releases": [],
        "branches": [],
        "refers": [],
    }
    unsent: set[str] = set()
    kept: list[Branch] = []
    for branch_index, branch in enumerate(family.branches):
        before = branch.model_dump(mode="json")
        whole = branch.condition_item_id == item_id
        actions: list[dict[str, Any]] = []
        releases: list[dict[str, Any]] = []
        for o, outcome in enumerate(branch.outcomes):
            for a, action in enumerate(outcome.actions):
                if whole or item_id in action.basis_ids:
                    actions.append(
                        {
                            "branch_id": branch.id,
                            "o": o,
                            "a": a,
                            "action": action.model_dump(mode="json"),
                        }
                    )
                    unsent.add(sent_item_id(branch.id, o, a))
                    outcome.actions[a] = Action(part=action.part, type=ActionType.NOTHING)
                elif item_id in action.release:
                    releases.append({"branch_id": branch.id, "o": o, "a": a})
                    action.release = [other for other in action.release if other != item_id]
        has_action = any(
            action.type != ActionType.NOTHING
            for outcome in branch.outcomes
            for action in outcome.actions
        )
        if has_action:
            kept.append(branch)
            undo["actions"] += actions
            undo["releases"] += releases
        else:
            undo["branches"].append({"index": branch_index, "branch": before})
    family.branches = kept

    remaining = []
    for item in family.items:
        if item.id == item_id:
            continue
        if item.id in unsent and item.status in (Status.TO_ASK, Status.UNCONFIRMED):
            undo["also_removed"].append(item.model_dump(mode="json"))
            continue
        if item.refers_to == item_id:
            undo["refers"].append(item.id)
            item = item.model_copy(update={"refers_to": None})
        remaining.append(item)
    family.items = remaining
    return undo


def restore_item(undo: dict[str, Any], family: Family) -> None:
    """消した項目と、一緒に取り下げた案を戻す。その後に変わったものは上書きしない。"""
    known = {item.id for item in family.items}
    restored = Item.model_validate(undo["item"])
    if restored.id not in known:
        family.items.insert(min(undo["index"], len(family.items)), restored)
        known.add(restored.id)
    for raw in undo["also_removed"]:
        also = Item.model_validate(raw)
        if also.id not in known:
            family.items.append(also)
            known.add(also.id)
    for position, item in enumerate(family.items):
        if item.id in undo["refers"] and item.refers_to is None:
            family.items[position] = item.model_copy(update={"refers_to": restored.id})

    present = {branch.id for branch in family.branches}
    for entry in undo["branches"]:
        branch = Branch.model_validate(entry["branch"])
        if branch.id not in present:
            family.branches.insert(min(entry["index"], len(family.branches)), branch)
            present.add(branch.id)

    by_id = {branch.id: branch for branch in family.branches}
    for entry in undo["actions"]:
        branch = by_id.get(entry["branch_id"])
        if branch is None:
            continue
        actions = branch.outcomes[entry["o"]].actions
        if actions[entry["a"]].type == ActionType.NOTHING:
            actions[entry["a"]] = Action.model_validate(entry["action"])
    for entry in undo["releases"]:
        branch = by_id.get(entry["branch_id"])
        if branch is None:
            continue
        action = branch.outcomes[entry["o"]].actions[entry["a"]]
        if restored.id not in action.release:
            action.release.append(restored.id)
