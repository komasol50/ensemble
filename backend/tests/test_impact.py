"""影響マップ：線・カード・変更の前後が同じ台帳から描かれ、データの無い線が無い。"""

from ensemble.impact import CHANGE, KEEP, RELEASE, impact_view
from ensemble.ledger import (
    Action,
    ActionType,
    Branch,
    CandidateDay,
    Certainty,
    Family,
    Item,
    Kind,
    Outcome,
    Part,
    Profile,
    Status,
    sent_item_id,
)

DOCTOR = "先生：明日の採血しだい。よければ木曜に帰れるかも"
RESULT = "今日の採血はだめでした"


def item(item_id: str, part: Part, **values: object) -> Item:
    fields: dict = {
        "id": item_id,
        "kind": Kind.ACTION,
        "part": part,
        "certainty": Certainty.CONFIRMED,
        "status": Status.TO_ASK,
    }
    return Item(**{**fields, **values})


def base_items() -> list[Item]:
    return [
        item(
            "doctor",
            Part.HOSPITAL,
            kind=Kind.INFO,
            status=None,
            text_original=DOCTOR,
            speaker="医師",
        ),
        item(
            "result",
            Part.HOSPITAL,
            kind=Kind.INFO,
            status=None,
            text_original=RESULT,
            speaker="親",
            refers_to="doctor",
        ),
        item(
            "q",
            Part.HOSPITAL,
            kind=Kind.QUESTION,
            status=Status.UNCONFIRMED,
            task_summary="退院の時刻",
        ),
        item("old", Part.CHILDCARE, status=Status.ASKED, task_summary="木の延長の確認"),
        item("boss", Part.WORK, task_summary="会議の連絡"),
    ]


def branch(*actions: Action, **values: object) -> Branch:
    return Branch(
        id="b1",
        condition_item_id="doctor",
        candidate_days=[
            CandidateDay(day="木曜", certainty=Certainty.LIKELY, cancelled=True),
            CandidateDay(day="火曜", certainty=Certainty.LIKELY),
        ],
        outcomes=[Outcome(label="だめだった", actions=list(actions))],
        **values,
    )


def family(items: list[Item], branches: list[Branch], aliases: dict | None = None) -> Family:
    return Family(
        code="c" * 43,
        profile=Profile(name="家庭"),
        items=items,
        branches=branches,
        aliases=aliases or {},
    )


def nodes_of(view: dict) -> dict[str, dict]:
    return {node["part"]: node for node in view["nodes"]}


CHILDCARE_ACTION = Action(
    part=Part.CHILDCARE,
    type=ActionType.CHECK_AVAILABILITY,
    draft="木曜は取り消し、火曜の空きを確認します",
    basis_ids=["old", "doctor", "result"],
    release=["old"],
)


def test_no_branch_is_none() -> None:
    assert impact_view(family(base_items(), [])) is None


def test_effects_come_from_the_branch() -> None:
    view = impact_view(family(base_items(), [branch(CHILDCARE_ACTION)]))
    nodes = nodes_of(view)
    assert nodes["保育園・預け先"]["effects"] == [RELEASE, CHANGE]
    assert nodes["職場"]["effects"] == [KEEP]
    assert nodes["病院"]["effects"] == [KEEP]


def test_no_line_without_data() -> None:
    """項目も案も無い相手は描かない。"""
    view = impact_view(family(base_items(), [branch(CHILDCARE_ACTION)]))
    assert set(nodes_of(view)) == {"職場", "保育園・預け先", "病院"}


def test_new_message_to_someone_without_items_is_drawn() -> None:
    action = Action(
        part=Part.SUPPORT,
        type=ActionType.CHECK_AVAILABILITY,
        draft="空きはありますか",
        basis_ids=["result"],
    )
    view = impact_view(family(base_items(), [branch(action)]))
    assert nodes_of(view)["支援"]["effects"] == [CHANGE]


def test_nodes_keep_the_fixed_order() -> None:
    items = base_items() + [item("f", Part.FAMILY, task_summary="持ち物")]
    view = impact_view(family(items, [branch(CHILDCARE_ACTION)]))
    assert [node["part"] for node in view["nodes"]] == ["家族", "職場", "保育園・預け先", "病院"]


def test_before_and_after_and_basis() -> None:
    made = item(
        sent_item_id("b1", 0, 0),
        Part.CHILDCARE,
        status=Status.ASKED,
        text_original=CHILDCARE_ACTION.draft,
    )
    view = impact_view(family(base_items() + [made], [branch(CHILDCARE_ACTION)]))
    node = nodes_of(view)["保育園・預け先"]
    assert node["before"] == ["木の延長の確認"]
    assert node["after"] == [{"text": CHILDCARE_ACTION.draft, "state": "返答待ち"}]
    assert {"speaker": "医師", "text": DOCTOR} in node["basis"]
    assert {"speaker": "あなた", "text": RESULT} in node["basis"]
    assert node["summary"].startswith("外す：")


def test_generated_item_does_not_count_as_kept_or_before() -> None:
    action = Action(
        part=Part.FAMILY,
        type=ActionType.REQUEST,
        draft="着替えを持ってきて",
        basis_ids=["result"],
    )
    made = item(sent_item_id("b1", 0, 0), Part.FAMILY, text_original=action.draft)
    view = impact_view(family(base_items() + [made], [branch(action)]))
    node = nodes_of(view)["家族"]
    assert node["effects"] == [CHANGE] and node["before"] == []
    assert node["after"][0]["state"] == "未依頼"


def test_kept_lists_what_is_still_open() -> None:
    items = base_items() + [item("done", Part.WORK, status=Status.DONE, task_summary="済んだ連絡")]
    view = impact_view(family(items, [branch(CHILDCARE_ACTION)]))
    work = nodes_of(view)["職場"]
    assert work["kept"] == [{"text": "会議の連絡", "state": "未依頼"}]
    assert work["summary"] == "会議の連絡：未依頼"


def test_withdrawn_action_draws_no_change() -> None:
    withdrawn = Action(part=Part.WORK, type=ActionType.NOTHING)
    view = impact_view(
        family(
            base_items(),
            [
                branch(
                    CHILDCARE_ACTION,
                ),
                branch(withdrawn),
            ],
        )
    )
    assert nodes_of(view)["職場"]["effects"] == [KEEP]


def test_headline_and_top_basis() -> None:
    view = impact_view(family(base_items(), [branch(CHILDCARE_ACTION)]))
    assert view["headline"] == "木曜の退院候補を取り消し"
    assert view["basis"] == [
        {"speaker": "医師", "text": DOCTOR},
        {"speaker": "あなた", "text": RESULT},
    ]


def test_headline_without_a_cancelled_day_is_the_label() -> None:
    plain = Branch(
        id="b1",
        condition_item_id="doctor",
        outcomes=[Outcome(label="よければ", actions=[]), Outcome(label="だめなら", actions=[])],
    )
    assert impact_view(family(base_items(), [plain]))["headline"] == "よければ／だめなら"


def test_latest_branch_is_used() -> None:
    first = Branch(id="b0", condition_item_id="doctor", outcomes=[])
    view = impact_view(family(base_items(), [first, branch(CHILDCARE_ACTION)]))
    assert nodes_of(view)["保育園・預け先"]["effects"] == [RELEASE, CHANGE]


def test_aliases_are_shown_as_names() -> None:
    action = Action(
        part=Part.WORK,
        type=ActionType.TELL,
        draft="［上司］へ連絡します",
        basis_ids=["result"],
    )
    view = impact_view(family(base_items(), [branch(action)], {"山田": "［上司］"}))
    assert nodes_of(view)["職場"]["after"][0]["text"] == "山田へ連絡します"
