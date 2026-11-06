"""図（新UI_2026-11-05）の筋書きに揃えたところの試験。"""

from ensemble.api import superseded_item_ids
from ensemble.ledger import (
    Action,
    ActionType,
    Branch,
    Certainty,
    Item,
    Kind,
    Outcome,
    Part,
    Status,
)
from ensemble.presets import load_family_from_preset


def _request() -> Item:
    return Item(
        id="i-request",
        kind=Kind.ACTION,
        part=Part.FAMILY,
        certainty=Certainty.CONFIRMED,
        text_original="着替えも頼みたい。場所は寝室の棚。",
        speaker="親",
        status=Status.TO_ASK,
        recorded_at="2026-11-09T15:20:00+09:00",
    )


def _branch(*actions: Action) -> Branch:
    return Branch(
        id="b-1",
        condition_item_id="i-request",
        candidate_days=[],
        outcomes=[Outcome(label="再検査になった", actions=list(actions))],
    )


def test_a_request_carried_into_the_later_draft_is_not_sent_twice() -> None:
    """夫への文が「着替えも頼みたい」を根拠に引き継いだら、親の前の文は送る一覧から外れる。"""
    family = load_family_from_preset("shoot_a")
    family.items.append(_request())
    family.branches.append(
        _branch(
            Action(
                part=Part.FAMILY,
                type=ActionType.REQUEST,
                draft="寝室の棚の着替えを持ってきてもらえる？",
                basis_ids=["i-request"],
            )
        )
    )
    assert "i-request" in superseded_item_ids(family)


def test_a_request_stays_when_another_person_cites_it() -> None:
    """別の相手への文が根拠に挙げても、夫への頼みごとはそのまま送れる。"""
    family = load_family_from_preset("shoot_a")
    family.items.append(_request())
    family.branches.append(
        _branch(
            Action(
                part=Part.WORK,
                type=ActionType.TELL,
                draft="退院日はまだ決まっていません。",
                basis_ids=["i-request"],
            )
        )
    )
    assert "i-request" not in superseded_item_ids(family)
