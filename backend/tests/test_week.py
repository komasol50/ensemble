"""週の表：台帳に書かれた日だけを、原文のまま置く。"""

from datetime import date

import pytest

from ensemble.guards import lower_certainty
from ensemble.ledger import (
    Branch,
    CandidateDay,
    Certainty,
    Family,
    Item,
    Kind,
    Medical,
    Part,
    Profile,
    Status,
)
from ensemble.week import (
    COLUMNS,
    dates_in,
    doctor_view,
    result_options,
    split_clauses,
    week_view,
)

TUESDAY = date(2026, 11, 3)
DOCTOR = "先生：明日の採血しだい。よければ木曜に帰れるかも。だめなら月曜にもう一回とって、それで大丈夫なら火曜かな"


def item(item_id: str = "x", **values: object) -> Item:
    fields: dict = {
        "id": item_id,
        "kind": Kind.INFO,
        "part": Part.HOSPITAL,
        "certainty": Certainty.LIKELY,
        "recorded_at": "2026-11-03T09:00:00+09:00",
    }
    return Item(**{**fields, **values})


def family(items: list[Item], branches: list[Branch] | None = None) -> Family:
    return Family(
        code="c" * 43,
        profile=Profile(name="家庭"),
        items=items,
        branches=branches or [],
    )


def cells_of(week: dict, label: str, column: str) -> list[dict]:
    row = next(row for row in week["rows"] if row["label"] == label)
    return row["cells"][COLUMNS.index(column)]


def test_clauses_keep_listed_weekdays_together() -> None:
    assert split_clauses("先生：明日の採血。木曜かも") == ["明日の採血", "木曜かも"]
    assert split_clauses("月、火の空きを確認") == ["月、火の空きを確認"]


def test_dates_from_the_day_it_was_said() -> None:
    assert dates_in("明日の採血", TUESDAY) == [date(2026, 11, 4)]
    assert dates_in("木曜に帰れるかも", TUESDAY) == [date(2026, 11, 5)]
    assert dates_in("月曜にもう一回", TUESDAY) == [date(2026, 11, 9)]
    # 同じ曜日は次の週。
    assert dates_in("火曜かな", TUESDAY) == [date(2026, 11, 10)]
    assert dates_in("月・火の空き", TUESDAY) == [date(2026, 11, 9), date(2026, 11, 10)]
    assert dates_in("11/7に面会", TUESDAY) == [date(2026, 11, 7)]
    assert dates_in("来週の木曜", TUESDAY) == [date(2026, 11, 12)]
    assert dates_in("3日分の着替え", TUESDAY) == []
    assert dates_in("検査をします", TUESDAY) == []


def test_impossible_dates_are_dropped() -> None:
    assert dates_in("31日に", date(2026, 11, 3)) == []
    assert dates_in("13/45に", TUESDAY) == []


def test_nothing_to_draw_is_none() -> None:
    assert week_view(family([item(text_original="様子を見ます")]), TUESDAY) is None


def test_rows_are_today_and_the_next_seven_days() -> None:
    week = week_view(family([item(text_original="明日の採血")]), TUESDAY)
    labels = [row["label"] for row in week["rows"]]
    assert labels == ["今日", "水", "木", "金", "土", "日", "月", "火"]
    assert [row["today"] for row in week["rows"]][:2] == [True, False]


def test_cell_is_the_original_clause() -> None:
    week = week_view(family([item(text_original=DOCTOR)]), TUESDAY)
    first = cells_of(week, "水", "病院")
    assert [(cell["text"], cell["style"]) for cell in first] == [("明日の採血しだい", "plain")]
    assert first[0]["part"] == "病院" and first[0]["item_id"]
    assert cells_of(week, "月", "病院")[0]["text"] == "だめなら月曜にもう一回とって"


def test_candidates_show_certainty_and_keep_the_cancelled_day() -> None:
    doctor = item("doctor", text_original=DOCTOR, condition="採血の結果")
    branch = Branch(
        id="b",
        condition_item_id="doctor",
        candidate_days=[
            CandidateDay(day="木曜", certainty=Certainty.LIKELY, cancelled=True),
            CandidateDay(day="火曜", certainty=Certainty.LIKELY),
        ],
    )
    week = week_view(family([doctor], [branch]), TUESDAY)
    assert cells_of(week, "木", "退院") == [{"text": "取り消し", "style": "cancelled"}]
    assert cells_of(week, "火", "退院") == [{"text": "候補", "style": "likely"}]
    # 退院候補の日は、医師の同じ言葉を病院の列に重ねない。
    assert cells_of(week, "木", "病院") == []
    assert cells_of(week, "月", "病院") != []


def test_proposal_is_drawn_before_it_is_accepted() -> None:
    doctor = item("doctor", text_original=DOCTOR)
    branch = Branch(
        id="b",
        condition_item_id="doctor",
        candidate_days=[CandidateDay(day="火曜", certainty=Certainty.LIKELY)],
    )
    board = family([doctor])
    assert cells_of(week_view(board, TUESDAY, branch), "火", "退院")[0]["text"] == "候補"
    assert board.branches == []


def test_candidate_that_is_not_a_day_stays_visible() -> None:
    doctor = item("doctor", text_original="来週前半かな")
    branch = Branch(
        id="b",
        condition_item_id="doctor",
        candidate_days=[CandidateDay(day="来週前半", certainty=Certainty.LIKELY)],
    )
    week = week_view(family([doctor], [branch]), TUESDAY)
    assert week["others"] == ["来週前半（候補）"]


def test_parts_go_to_their_columns_and_own_errands_are_left_out() -> None:
    week = week_view(
        family(
            [
                item("a", part=Part.CHILDCARE, text_original="木曜の延長の空きを確認した"),
                item("b", part=Part.SUPPORT, text_original="金曜の迎えの空き"),
                item("c", part=Part.WORK, task_summary="木曜の会議の連絡"),
                item("d", part=Part.FAMILY, text_original="水曜の夜に洗濯物"),
                item("e", part=Part.PARENT, text_original="土曜に売店"),
            ]
        ),
        TUESDAY,
    )
    assert cells_of(week, "木", "家・園")[0]["text"] == "木曜の延長の空きを確認した"
    assert cells_of(week, "金", "家・園")[0]["text"] == "金曜の迎えの空き"
    assert cells_of(week, "木", "仕事")[0]["text"] == "木曜の会議の連絡"
    assert cells_of(week, "水", "家・園")[0]["text"] == "水曜の夜に洗濯物"
    assert not any(
        cell for cell in sum((row["cells"] for row in week["rows"]), []) if "売店" in str(cell)
    )


def test_finished_and_medical_questions_are_left_out() -> None:
    board = family(
        [
            item("a", kind=Kind.ACTION, status=Status.DONE, text_original="木曜に確認"),
            item("b", kind=Kind.QUESTION, medical=Medical.ASK, text_original="木曜に薬は"),
        ]
    )
    assert week_view(board, TUESDAY) is None


def test_released_item_is_marked() -> None:
    from ensemble.ledger import Action, ActionType, Outcome

    kept = item("a", part=Part.CHILDCARE, kind=Kind.ACTION, text_original="木曜の延長")
    branch = Branch(
        id="b",
        condition_item_id="a",
        outcomes=[
            Outcome(
                label="だめ",
                actions=[
                    Action(part=Part.CHILDCARE, type=ActionType.NOTHING, release=["a"]),
                ],
            )
        ],
    )
    week = week_view(family([kept], [branch]), TUESDAY)
    assert cells_of(week, "木", "家・園")[0]["style"] == "released"


def test_future_recorded_day_counts_as_today() -> None:
    """プリセットの記録の日が未来でも、予定を落とさない。"""
    future = item(text_original="明日の採血", recorded_at="2026-12-25T09:00:00+09:00")
    week = week_view(family([future]), TUESDAY)
    assert cells_of(week, "水", "病院")[0]["text"] == "明日の採血"


def test_old_statement_is_placed_from_when_it_was_said() -> None:
    """日曜に「明日」と言われたものは、火曜の表では過ぎているので描かない。"""
    old = item(text_original="明日の採血", recorded_at="2026-11-01T09:00:00+09:00")
    assert week_view(family([old]), TUESDAY) is None


def test_aliases_are_shown_as_names() -> None:
    board = family([item(text_original="木曜に［子］の採血")])
    board.aliases = {"たろう": "［子］"}
    week = week_view(board, TUESDAY)
    assert cells_of(week, "木", "病院")[0]["text"] == "木曜にたろうの採血"


def test_family_item_without_a_day_is_not_drawn() -> None:
    """病室に誰がいるかの予定は描かない。日付の入った用事の原文だけ。"""
    week = week_view(
        family(
            [
                item("d", part=Part.FAMILY, text_original="水曜の夜に洗濯物"),
                item("e", part=Part.FAMILY, text_original="着替えをお願い"),
            ]
        ),
        TUESDAY,
    )
    assert "水曜の夜に洗濯物" in str(week["rows"])
    assert "着替えをお願い" not in str(week["rows"])


def test_rows_have_the_date_and_weekday() -> None:
    week = week_view(family([item(text_original="明日の採血")]), TUESDAY)
    assert week is not None
    assert week["rows"][0]["date"] == "11/3"
    assert week["rows"][0]["weekday"] == "火"


def test_same_item_appears_once_a_day() -> None:
    both = item(part=Part.FAMILY, text_original="水曜の朝に洗濯物。水曜の夜に着替え", due="水曜")
    other = item("y", part=Part.FAMILY, text_original="水曜の夜に充電器")
    week = week_view(family([both, other]), TUESDAY)
    assert week is not None
    cells = cells_of(week, "水", "家・園")
    assert [cell["item_id"] for cell in cells] == ["x", "y"]
    assert cells[0]["text"] == "水曜の朝に洗濯物"


DOCTOR_WORDS = (
    "先生：明日の採血しだい。よければ木曜に帰れるかも。"
    "だめなら月曜にもう一回とって、それで大丈夫なら火曜かな"
)


def test_result_options_keep_the_doctors_words() -> None:
    options = result_options(DOCTOR_WORDS, date(2026, 11, 3), date(2026, 11, 3))
    assert [option["label"] for option in options] == [
        "よければ木曜に帰れるかも",
        "だめなら月曜にもう一回とって、それで大丈夫なら火曜かな",
    ]
    assert options[1]["days"] == ["11/9（月）", "11/10（火）"]


def test_result_text_says_today_only_on_that_day() -> None:
    # 最初の節の日（明日の採血＝11/4）が今日なら、評価の入力と同じ「今日の採血はだめでした」。
    on_the_day = result_options(DOCTOR_WORDS, date(2026, 11, 3), date(2026, 11, 4))
    assert on_the_day[1]["result"] == "今日の採血はだめでした"
    assert on_the_day[0]["result"] == "今日の採血はよかったです"
    # 別の日に押したら「今日の」と書かない。後から読んでも誤った記録にならない。
    later = result_options(DOCTOR_WORDS, date(2026, 11, 3), date(2026, 11, 6))
    assert later[1]["result"] == "『明日の採血』はだめでした"
    assert later[0]["result"] == "『明日の採血』はよかったです"
    # 別の日に押した文の「明日」を、押した日から数えて表に置かない。
    result = Item(
        id="r",
        kind=Kind.INFO,
        part=Part.HOSPITAL,
        certainty=Certainty.CONFIRMED,
        text_original=later[1]["result"],
        refers_to="d",
        recorded_at="2026-11-06T10:00:00+09:00",
    )
    view = week_view(_doctor_family(result), date(2026, 11, 6))
    assert view is not None
    assert all(
        "はだめでした" not in cell["text"]
        for row in view["rows"]
        for col in row["cells"]
        for cell in col
    )


@pytest.mark.parametrize("today", [date(2026, 11, 3), date(2026, 11, 4), date(2026, 11, 6)])
def test_result_text_stays_certain(today: date) -> None:
    # 確かさの守りで「見込み」に下がると、案が確認だけになる。
    options = result_options(DOCTOR_WORDS, date(2026, 11, 3), today)
    for option in options:
        item = Item(
            id="x",
            kind=Kind.INFO,
            part=Part.HOSPITAL,
            certainty=Certainty.CONFIRMED,
            text_original=option["result"],
        )
        assert lower_certainty(item).certainty == Certainty.CONFIRMED


@pytest.mark.parametrize(
    "text",
    [
        "先生：来週また話しましょう",
        "先生：よければ木曜に帰れるかも",
        "先生：下がれば木曜に帰れるかも。下がらなければ月曜かな",
        # 何の結果かが書いていないので、結果の文を作れない。
        "先生：よければ木曜に帰れるかも。だめなら月曜かな",
    ],
)
def test_result_options_are_empty_when_two_cannot_be_made(text: str) -> None:
    assert result_options(text, date(2026, 11, 3), date(2026, 11, 3)) == []


def _doctor_family(*extra: Item, branches: list[Branch] | None = None) -> Family:
    doctor = Item(
        id="d",
        kind=Kind.INFO,
        part=Part.HOSPITAL,
        certainty=Certainty.LIKELY,
        text_original=DOCTOR_WORDS,
        condition="採血の結果",
        recorded_at="2026-11-03T10:00:00+09:00",
    )
    return Family(
        code="c" * 32,
        profile=Profile(name="家庭"),
        items=[doctor, *extra],
        branches=branches or [],
    )


def test_doctor_view_before_a_plan_has_no_candidate_marks() -> None:
    view = doctor_view(_doctor_family(), date(2026, 11, 3))
    assert view is not None
    assert view["open"] is True and view["due"] is False
    assert view["candidates"] == []
    assert view["clauses"][0] == {"text": "明日の採血しだい", "days": ["11/4（水）"]}


def test_doctor_view_is_due_on_the_first_day() -> None:
    view = doctor_view(_doctor_family(), date(2026, 11, 4))
    assert view is not None and view["due"] is True


def test_doctor_view_closes_when_a_result_is_in() -> None:
    result = Item(
        id="r",
        kind=Kind.INFO,
        part=Part.HOSPITAL,
        certainty=Certainty.CONFIRMED,
        text_original="今日の採血はだめでした",
        refers_to="d",
    )
    view = doctor_view(_doctor_family(result), date(2026, 11, 4))
    assert view is not None and view["open"] is False


def test_doctor_view_shows_only_the_plans_candidates() -> None:
    branch = Branch(
        id="b",
        condition_item_id="d",
        candidate_days=[
            CandidateDay(day="木曜", certainty=Certainty.LIKELY, cancelled=True),
            CandidateDay(day="火曜", certainty=Certainty.LIKELY),
        ],
        outcomes=[],
    )
    view = doctor_view(_doctor_family(branches=[branch]), date(2026, 11, 4))
    assert view is not None
    assert view["candidates"] == [
        {"day": "11/5（木）", "certainty": "見込み", "cancelled": True},
        {"day": "11/10（火）", "certainty": "見込み", "cancelled": False},
    ]


def test_doctor_view_skips_a_medical_question() -> None:
    family = _doctor_family()
    family.items[0].medical = Medical.ASK
    assert doctor_view(family, date(2026, 11, 4)) is None
