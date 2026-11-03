"""影響マップ：線・カード・変更の前後が同じ台帳から描かれ、データの無い線が無い。"""

from ensemble.impact import CHANGE, KEEP, RECORDED_HEADLINE, RELEASE, _first_sentence, impact_view
from ensemble.ledger import (
    Action,
    ActionType,
    Branch,
    CandidateDay,
    Certainty,
    ErrandPerson,
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
    assert node["task"] == "保育園に空きを確認する"
    assert "→ 取り消し" in node["change"]


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
    assert work["task"] == "変更なし"


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


def test_headline_without_a_cancelled_day_is_a_fixed_text() -> None:
    plain = Branch(
        id="b1",
        condition_item_id="doctor",
        outcomes=[Outcome(label="よければ", actions=[]), Outcome(label="だめなら", actions=[])],
    )
    assert impact_view(family(base_items(), [plain]))["headline"] == RECORDED_HEADLINE


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


def test_task_says_what_the_parent_does() -> None:
    """カードには、見た人が何をするかを書く。"""
    profile = Profile(name="家庭", errand_people=[ErrandPerson(label="夫", availability="夜")])
    actions = [
        Action(part=Part.FAMILY, type=ActionType.REQUEST, draft="持ってきて", basis_ids=["result"]),
        Action(part=Part.WORK, type=ActionType.TELL, draft="伝えます", basis_ids=["result"]),
        Action(part=Part.HOSPITAL, type=ActionType.CONFIRM, draft="聞きます", basis_ids=["result"]),
        Action(
            part=Part.SUPPORT,
            type=ActionType.CHECK_AVAILABILITY,
            draft="空き",
            basis_ids=["result"],
        ),
    ]
    board = family(base_items(), [branch(*actions)])
    board.profile = profile
    nodes = nodes_of(impact_view(board))
    assert nodes["家族"]["task"] == "夫に頼む" and nodes["家族"]["who"] == "家族（夫）"
    assert nodes["職場"]["task"] == "職場に伝える" and nodes["職場"]["who"] == "職場"
    assert nodes["病院"]["task"] == "病院に確認する"
    assert nodes["支援"]["task"] == "支援に空きを確認する"


def test_release_only_task() -> None:
    action = Action(part=Part.CHILDCARE, type=ActionType.NOTHING, release=["old"])
    node = nodes_of(impact_view(family(base_items(), [branch(action)])))["保育園・預け先"]
    assert node["effects"] == [RELEASE] and node["task"] == "前の確認を取り消す"


def test_only_released_items_are_shown_as_cancelled() -> None:
    """取り消していない用件を「取り消す確認」に出さない。"""
    items = base_items() + [item("f", Part.FAMILY, task_summary="持ち物の確認")]
    action = Action(
        part=Part.FAMILY, type=ActionType.REQUEST, draft="持ってきて", basis_ids=["result"]
    )
    node = nodes_of(impact_view(family(items, [branch(action)])))["家族"]
    assert node["before"] == []


def test_change_line_for_a_new_request_skips_the_greeting() -> None:
    assert _first_sentence("お世話になっております。火曜の空きを教えてください。") == (
        "火曜の空きを教えてください。"
    )
    assert _first_sentence("お疲れさまです。") == "お疲れさまです。"


def test_cause_is_the_parents_own_words() -> None:
    view = impact_view(family(base_items(), [branch(CHILDCARE_ACTION)]))
    assert view["cause"] == RESULT


def test_no_cause_before_the_result() -> None:
    items = [item for item in base_items() if item.text_original != RESULT]
    assert impact_view(family(items, [branch(CHILDCARE_ACTION)]))["cause"] is None
