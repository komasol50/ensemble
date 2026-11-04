"""親が項目を直すことと、項目に起きたことの呼び方。AIは呼ばない。

台帳の欄は増やさない。直した文にも、取り込みと同じ守りを当て直す。
送った後の文は直せない。相手の手元の文と、ボードの文が食い違うため。
"""

from collections.abc import Sequence
from typing import Literal

from pydantic import Field

from .guards import (
    MEDICAL_WORD_ERROR,
    PARENT_SPEAKERS,
    check_input_length,
    check_medical_words,
    find_symptom_words,
    is_own_note,
    is_third_party,
    lower_certainty,
    mark_symptom_as_ask,
    mask_aliases,
)
from .intake import DUE_MAX_CHARS, require_due_for_time
from .ledger import Family, Item, Kind, Medical, Part, Status, _Strict, sent_item_id, status_label
from .store import ItemEvent

OWNER_MAX_CHARS = 20
EDITED_LABEL = "直した"
UNDONE_LABEL = "元に戻した"
CANNOT_SEND_MEDICAL_TEXT = "医療の語が入っている文は送れません。その部分を消してください"
SENT_TEXT_IS_FIXED = "送った後の文は直せません。相手の手元の文と食い違うためです"
NO_ORIGINAL = "この項目は原文を保存していません。用件を直してください"
TOO_LONG = "2,000字までです"
EMPTY_TEXT = "文が空です"
CANNOT_MOVE_PROPOSAL = "案から作った連絡は、相手を変えられません"
CANNOT_MOVE_OWN_NOTE = "自分のことの一言は、相手を変えられません"
CANNOT_MOVE_THIRD_PARTY = "この文は原文を残せない相手には移せません"
CANNOT_MOVE_SENT = "送った後の連絡は、相手を変えられません"
REFERENCED_ORIGINAL_IS_KEPT = (
    "この文につながる案や用件があるので、そのまま残します。違っていたら消して、入れ直してください"
)
DUE_IS_THE_REPLY = "この時刻は返事の内容です。［返事を記録］から直してください"
NOT_EDITABLE = "この項目には使えません"
UNSENT_STATUSES = (Status.TO_ASK, Status.UNCONFIRMED)
FINISHED_STATUSES = (Status.DONE, Status.DELIVERED, Status.ANSWERED)
REPLY_STATUSES = (Status.ACCEPTED, Status.DIFFICULT, Status.ON_HOLD, Status.OK_WITH_TIME)
UNDOABLE_STATUSES = FINISHED_STATUSES + REPLY_STATUSES + (Status.ASKED,)


class EditRefused(ValueError):
    """直せない理由。そのまま画面に出す文。入力の中身は含めない。"""


class ItemEdit(_Strict):
    """直す欄だけを送る。送らなかった欄は変えない。"""

    version: int
    text_original: str | None = None
    task_summary: str | None = None
    part: Literal[Part.HOSPITAL, Part.FAMILY, Part.CHILDCARE, Part.WORK, Part.SUPPORT] | None = None
    owner: str | None = Field(default=None, max_length=OWNER_MAX_CHARS)
    due: str | None = Field(default=None, max_length=DUE_MAX_CHARS)


def is_from_proposal(item: Item) -> bool:
    return item.id.startswith("s-")


def is_outgoing(item: Item) -> bool:
    """相手へ送る文か。案から作った連絡と、親が家族に頼みたいと入れた文。"""
    return is_from_proposal(item) or (
        item.kind == Kind.ACTION and item.part == Part.FAMILY and item.speaker == "親"
    )


def first_label(item: Item) -> str:
    return "文を作った" if is_from_proposal(item) else "入れた"


def event_label(item: Item) -> str:
    """状態が変わったときの呼び方。変わった後の項目から作る。"""
    status = item.status
    if status == Status.ASKED:
        return "送った"
    if status in FINISHED_STATUSES:
        return "病棟に聞いた" if item.medical == Medical.ASK else "済"
    if status in UNSENT_STATUSES or status is None:
        return UNDONE_LABEL
    label = status_label(status, item.due, item.part)
    return f"{item.owner}：{label}" if item.part == Part.FAMILY and item.owner else label


def undo_target(item: Item, history: Sequence[ItemEvent]) -> tuple[Status, str | None]:
    """元に戻す先の状態と期日。記録を積み上げ、「元に戻した」で1つ降ろす。

    行の中の［元に戻す］は時間が経ってからも押せるので、画面の推測でなく記録で決める。
    """
    changes: list[ItemEvent] = []
    for event in history:
        if event.before_status is None:
            continue  # 直したなど、状態を変えていない記録
        if event.label != UNDONE_LABEL:
            changes.append(event)
        elif changes:
            changes.pop()
    if changes:
        status, due = Status(changes[-1].before_status), changes[-1].before_due
    elif item.kind == Kind.QUESTION:
        status, due = Status.UNCONFIRMED, None
    elif item.status in REPLY_STATUSES or (is_from_proposal(item) and item.status != Status.ASKED):
        status, due = Status.ASKED, None  # 返事があったなら、頼んだ後
    else:
        status, due = Status.TO_ASK, None
    # 期日が動くのは「◯時まで」の返事だけ。ほかは、後から直した期日を残す。
    return status, due if Status.OK_WITH_TIME in (item.status, status) else item.due


def _is_referenced(family: Family, item_id: str) -> bool:
    """ほかの項目や案が、この項目を根拠にしているか。"""
    return any(entry.refers_to == item_id for entry in family.items) or any(
        branch.condition_item_id == item_id
        or any(
            item_id in action.basis_ids for outcome in branch.outcomes for action in outcome.actions
        )
        for branch in family.branches
    )


def original_fixed_reason(family: Family, item: Item) -> str | None:
    """原文を直せない理由。親以外の言葉は原文で残し、根拠にした文は案と食い違わせない。"""
    if item.text_original is None:
        return None
    if item.speaker is not None and item.speaker not in PARENT_SPEAKERS:
        return f"{item.speaker}の言葉は原文のまま残します。違っていたら消して、入れ直してください"
    return REFERENCED_ORIGINAL_IS_KEPT if _is_referenced(family, item.id) else None


def _adds_symptom(text: str | None, before: str | None) -> bool:
    return bool(set(find_symptom_words(text or "")) - set(find_symptom_words(before or "")))


def _clean_text(text: str | None) -> str:
    cleaned = (text or "").strip()
    if not cleaned:
        raise EditRefused(EMPTY_TEXT)
    if not check_input_length(cleaned):
        raise EditRefused(TOO_LONG)
    return cleaned


def _sync_draft(family: Family, item: Item, text: str) -> list[Item]:
    """案の文も同じに直す（次にやることの画面は案の文を読む）。根拠の項目を返す。"""
    by_id = {entry.id: entry for entry in family.items}
    for branch in family.branches:
        for o, outcome in enumerate(branch.outcomes):
            for a, action in enumerate(outcome.actions):
                if sent_item_id(branch.id, o, a) == item.id:
                    action.draft = text
                    return [by_id[i] for i in action.basis_ids if i in by_id]
    return []


def apply_edit(family: Family, item_id: str, body: ItemEdit) -> bool:
    """台帳の項目を直す。変わったら True。直せないときは EditRefused。"""
    index = next(i for i, entry in enumerate(family.items) if entry.id == item_id)
    item = family.items[index]
    sent = body.model_fields_set
    update: dict[str, object] = {}
    aliases = family.aliases

    if "text_original" in sent:
        if item.text_original is None:
            raise EditRefused(NO_ORIGINAL)
        if is_outgoing(item) and item.status not in UNSENT_STATUSES:
            raise EditRefused(SENT_TEXT_IS_FIXED)
        original = mask_aliases(_clean_text(body.text_original), aliases)
        reason = original_fixed_reason(family, item) if original != item.text_original else None
        if reason:
            raise EditRefused(reason)
        update["text_original"] = original
    if "task_summary" in sent:
        update["task_summary"] = mask_aliases(_clean_text(body.task_summary), aliases)

    if "part" in sent:
        if body.part is None:
            raise EditRefused(NOT_EDITABLE)
        if body.part != item.part:
            if is_from_proposal(item):
                raise EditRefused(CANNOT_MOVE_PROPOSAL)
            if is_own_note(item):
                raise EditRefused(CANNOT_MOVE_OWN_NOTE)
            # 移すと夫のリンクが切れ、移した先で文も直せてしまう。
            if is_outgoing(item) and item.status not in UNSENT_STATUSES:
                raise EditRefused(CANNOT_MOVE_SENT)
            update["part"] = body.part
            # 家族の用事は、誰の答えかが要る。
            if (
                body.part == Part.FAMILY
                and item.kind == Kind.ACTION
                and not item.owner
                and family.profile.errand_people
            ):
                update["owner"] = family.profile.errand_people[0].label

    if "owner" in sent:
        update["owner"] = (body.owner or "").strip() or None
    if "due" in sent:
        due = (body.due or "").strip() or None
        if due != item.due and item.status == Status.OK_WITH_TIME:
            raise EditRefused(DUE_IS_THE_REPLY)
        update["due"] = due

    changed = item.model_copy(update=update)
    if changed == item:
        return False

    text_changed = (
        changed.text_original != item.text_original or changed.task_summary != item.task_summary
    )
    if text_changed:
        # 確かさは下げるだけ、医療の問いは外さない。取り込みと同じ守り。
        changed = mark_symptom_as_ask(lower_certainty(changed))
        # 取り込みの守りは原文があると用件を見ない。用件で増えた症状の語は親の言葉なので、
        # 別に確かめる。もとからある語の誤字を直しただけでは問いにしない（医師の説明が
        # 根拠から外れ、戻せなくなるため）。
        if _adds_symptom(changed.task_summary, item.task_summary):
            changed = changed.model_copy(update={"medical": Medical.ASK})
    if is_third_party(changed) and changed.text_original is not None:
        # 第三者の文は原文を保存しない約束なので、原文のある項目はそこへ移さない。
        raise EditRefused(CANNOT_MOVE_THIRD_PARTY)

    if changed.text_original != item.text_original and is_from_proposal(item):
        basis = _sync_draft(family, item, changed.text_original or "")
        if any(
            MEDICAL_WORD_ERROR in violation
            for violation in check_medical_words(changed.text_original, basis, changed.part)
        ):
            raise EditRefused(CANNOT_SEND_MEDICAL_TEXT)

    try:
        checked = Item.model_validate(changed.model_dump(mode="json"))
        require_due_for_time(checked)
    except ValueError as error:
        raise EditRefused(NOT_EDITABLE) from error
    family.items[index] = checked
    return True
