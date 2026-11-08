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
    after_title,
    COLUMNS,
    calendar_days,
    clause_days,
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
    options = result_options(DOCTOR_WORDS, date(2026, 11, 3))
    assert [option["label"] for option in options] == [
        "よければ木曜に帰れるかも",
        "だめなら月曜にもう一回とって、それで大丈夫なら火曜かな",
    ]
    assert options[1]["days"] == ["11/9（月）", "11/10（火）"]


def test_result_text_says_only_what_was_said() -> None:
    options = result_options(DOCTOR_WORDS, date(2026, 11, 3))
    assert options[0]["result"] == "木曜に帰れそうと言われた"
    assert options[1]["result"] == "月曜にもう一回とると言われた"


def test_result_text_keeps_the_doctors_uncertainty() -> None:
    options = result_options(DOCTOR_WORDS, date(2026, 11, 3))
    assert options[0]["certainty"] == "見込み"
    assert options[1]["certainty"] == "確定"


def test_result_text_stays_certain() -> None:
    # 確かさの守りで「見込み」に下がると、案が確認だけになる。
    for option in result_options(DOCTOR_WORDS, date(2026, 11, 3))[1:]:
        item = Item(
            id="x",
            kind=Kind.INFO,
            part=Part.HOSPITAL,
            certainty=Certainty.CONFIRMED,
            text_original=option["result"],
        )
        assert lower_certainty(item).certainty == Certainty.CONFIRMED


@pytest.mark.parametrize(
    ("recorded", "expected"),
    [
        # 月曜に開く：「それで火曜」は翌日でなく、「月曜にもう一回」の後の火曜。
        (date(2026, 11, 9), ["11/10（火）", "11/12（木）", "11/16（月）", "11/17（火）"]),
        # 水曜に開く：「よければ木曜」は採血と同じ日。動画の日付でもある。
        (date(2026, 11, 4), ["11/5（木）", "11/5（木）", "11/9（月）", "11/10（火）"]),
        # 日曜に開く：「だめなら月曜」は採血と同じ日でなく、その次の月曜。
        (date(2026, 11, 8), ["11/9（月）", "11/12（木）", "11/16（月）", "11/17（火）"]),
        # 木〜土に開く：だめの道は、よければの木曜より後。話の順を逆に読まない（QA Test）。
        (date(2026, 11, 12), ["11/13（金）", "11/19（木）", "11/23（月）", "11/24（火）"]),
        (date(2026, 11, 13), ["11/14（土）", "11/19（木）", "11/23（月）", "11/24（火）"]),
        (date(2026, 11, 14), ["11/15（日）", "11/19（木）", "11/23（月）", "11/24（火）"]),
    ],
)
def test_weekdays_are_read_in_the_order_of_the_story(recorded: date, expected: list[str]) -> None:
    family = _doctor_family()
    family.items[0].recorded_at = f"{recorded.isoformat()}T10:00:00+09:00"
    view = doctor_view(family, recorded)
    assert view is not None
    assert [clause["days"][0] for clause in view["clauses"]] == expected


@pytest.mark.parametrize(
    "text",
    [
        "先生：来週また話しましょう",
        "先生：よければ木曜に帰れるかも",
        "先生：下がれば木曜に帰れるかも。下がらなければ月曜かな",
        # 決まった言い換えに合わない。
        "先生：よければ木曜に帰れるかも。だめなら月曜かな",
    ],
)
def test_result_options_are_empty_when_two_cannot_be_made(text: str) -> None:
    assert result_options(text, date(2026, 11, 3)) == []


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
        text_original="月曜にもう一回とると言われた",
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


@pytest.mark.parametrize("offset", range(7))
def test_candidate_days_match_the_clauses_on_every_weekday(offset: int) -> None:
    """どの曜日に開いても、候補日は節の日と同じで、やり直しの日はよい結果の日と重ならない。"""
    recorded = date(2026, 11, 2 + offset)
    branch = Branch(
        id="b",
        condition_item_id="d",
        candidate_days=[
            CandidateDay(day="木曜", certainty=Certainty.LIKELY, cancelled=True),
            CandidateDay(day="火曜", certainty=Certainty.LIKELY),
        ],
    )
    family = _doctor_family(branches=[branch])
    family.items[0].recorded_at = f"{recorded.isoformat()}T10:00:00+09:00"
    view = doctor_view(family, recorded)
    assert view is not None
    by_clause = {clause["text"]: clause["days"][0] for clause in view["clauses"]}
    days = {entry["day"] for entry in view["candidates"]}
    assert by_clause["よければ木曜に帰れるかも"] in days
    assert by_clause["それで大丈夫なら火曜かな"] in days
    assert by_clause["だめなら月曜にもう一回とって"] not in days
    # 採血→よければ→だめ→それで、の順。よければだけは採血と同じ日でもよい。
    good = int(by_clause["よければ木曜に帰れるかも"].split("/")[1].split("（")[0])
    blood = int(by_clause["明日の採血しだい"].split("/")[1].split("（")[0])
    bad = int(by_clause["だめなら月曜にもう一回とって"].split("/")[1].split("（")[0])
    assert blood <= good < bad
    # やり直しの日は採血より後、その続きはさらに後。
    order = [
        int(by_clause[key].split("/")[1].split("（")[0])
        for key in ("明日の採血しだい", "だめなら月曜にもう一回とって", "それで大丈夫なら火曜かな")
    ]
    assert order == sorted(order) and len(set(order)) == 3


def test_the_shoot_day_shows_the_cancelled_thursday_in_the_strip() -> None:
    """撮影の日付（水曜）で、帯に取り消しの木曜と候補の火曜が出る（動画 1:23）。"""
    wednesday = date(2026, 11, 4)
    branch = Branch(
        id="b",
        condition_item_id="d",
        candidate_days=[
            CandidateDay(day="木曜", certainty=Certainty.LIKELY, cancelled=True),
            CandidateDay(day="火曜", certainty=Certainty.LIKELY),
        ],
    )
    family = _doctor_family(branches=[branch])
    family.items[0].recorded_at = "2026-11-04T10:00:00+09:00"
    view = week_view(family, wednesday)
    assert view is not None
    marks = {row["date"]: row["cells"][0][0]["text"] for row in view["rows"] if row["cells"][0]}
    assert marks == {"11/5": "取り消し", "11/10": "候補"}


def test_a_good_word_with_nakereba_is_not_read_as_bad() -> None:
    """「問題なければ」はよいほう。木曜に開くと、悪いほうが後になる。"""
    days = dict(
        clause_days(
            "先生：明日の採血しだい。問題なければ木曜かな。下がらなければ月曜かな",
            date(2026, 11, 12),
        )
    )
    assert days["問題なければ木曜かな"] == [date(2026, 11, 19)]
    assert days["下がらなければ月曜かな"] == [date(2026, 11, 23)]


def test_a_candidate_beyond_the_table_is_not_hidden() -> None:
    """月曜に開くと、候補の火曜は8日の表の外。表の外の行に残す。"""
    monday = date(2026, 11, 9)
    branch = Branch(
        id="b",
        condition_item_id="d",
        candidate_days=[
            CandidateDay(day="木曜", certainty=Certainty.LIKELY, cancelled=True),
            CandidateDay(day="火曜", certainty=Certainty.LIKELY),
        ],
    )
    family = _doctor_family(branches=[branch])
    family.items[0].recorded_at = "2026-11-09T10:00:00+09:00"
    view = week_view(family, monday)
    assert view is not None
    assert view["others"] == ["11/17（火）（候補）"]


@pytest.mark.parametrize(
    ("opened", "tuesday"),
    [
        (date(2026, 11, 4), "11/10"),  # 撮影の日付（水）。直す前と同じ。
        (date(2026, 11, 6), "11/17"),  # 金曜に開くと、候補の火曜は次の週。
        (date(2026, 11, 9), "11/17"),
    ],
)
def test_a_draft_sits_on_the_same_tuesday_as_the_candidate(opened: date, tuesday: str) -> None:
    """案から作った下書きの火曜は、候補の火曜と同じ日に出る（QA Test の P1）。"""
    branch = Branch(
        id="b",
        condition_item_id="d",
        candidate_days=[CandidateDay(day="火曜", certainty=Certainty.LIKELY)],
    )
    draft = Item(
        id="s-b-0-0",
        kind=Kind.ACTION,
        part=Part.CHILDCARE,
        certainty=Certainty.LIKELY,
        text_original="お世話になっております。火曜の延長保育の空きを確認させてください。",
        recorded_at=f"{opened.isoformat()}T10:05:00+09:00",
    )
    family = _doctor_family(draft, branches=[branch])
    family.items[0].recorded_at = f"{opened.isoformat()}T10:00:00+09:00"
    view = week_view(family, opened, days=calendar_days(opened))
    assert view is not None
    placed = [
        row["date"]
        for row in view["rows"]
        for column in row["cells"]
        for cell in column
        if cell.get("item_id") == "s-b-0-0"
    ]
    candidate = [row["date"] for row in view["rows"] if row["cells"][0]]
    assert placed == [tuesday]
    assert tuesday in candidate


FIGURE_WORDS = "先生から「12日に採血。退院できるなら16日が候補、再検査なら17日」と言われた。"


def test_the_quote_frame_is_not_part_of_a_clause() -> None:
    """「先生から「」「」と言われた」は親の言葉。節は医師の言葉だけにする。"""
    assert split_clauses(FIGURE_WORDS) == [
        "12日に採血",
        "退院できるなら16日が候補",
        "再検査なら17日",
    ]
    # 引用が節の中で閉じるときは触らない。「しだい」は待つ日の語。
    assert split_clauses("「明日の採血」しだい。") == ["「明日の採血」しだい"]


def test_figure_options_are_built_from_the_clause_words() -> None:
    """図の⑤：退院の予定になった（見込みのまま）／再検査になった（退院日は未定）。AIを使わない。"""
    first, second = result_options(FIGURE_WORDS, date(2026, 11, 9))
    assert first["title"] == "退院の予定になった"
    assert first["days"] == ["11/16（月）"]
    assert first["certainty"] == "見込み"
    assert first["label"] == "退院できるなら16日が候補"
    assert "16日" in first["result"]
    assert "note" not in first
    assert second["title"] == "再検査になった"
    assert second["days"] == ["11/17（火）"]
    assert second["certainty"] == "確定"
    assert second["note"] == "退院日は未定"
    # 入れる文は原文の日の語を持ち、条件の語を持たない（確定の結果が見込みに下がらない）。
    assert second["result"] == "17日に再検査になったと言われた"
    assert after_title(FIGURE_WORDS) == "採血のあと"


def test_a_test_followed_by_a_discharge_day_keeps_a_candidate() -> None:
    """検査の先に退院の日が続くなら、候補日が残るので「退院日は未定」を出さない。"""
    text = "退院できるなら16日、再検査なら17日に検査して、それで大丈夫なら19日"
    options = result_options(text, date(2026, 11, 9))
    assert len(options) == 2
    assert "note" not in options[1]


def test_the_old_wording_keeps_its_options() -> None:
    """家庭Aの言葉（曜日・語尾の言い換え）は、今までの選択肢のまま。"""
    options = result_options(DOCTOR_WORDS, date(2026, 11, 3))
    assert all("title" not in option for option in options)


@pytest.mark.parametrize(
    "text",
    [
        "12日に採血。退院できなければ17日に再検査、退院できるなら16日",
        "12日に採血。再検査にならなければ16日に退院、再検査なら17日",
        "12日に採血。採血の結果が悪ければ17日、退院できるなら16日",
        "12日に採血。退院が無理なら17日、退院できたら16日",
        "12日に採血。退院がのびたら18日、退院できるなら16日",
    ],
)
def test_negative_conditions_never_become_a_kind_option(text: str) -> None:
    """否定や悪い結果の条件を「退院の予定になった」「再検査になった」と逆に読まない（QA Test の P1）。

    合わない節は種類の選択肢にせず、言われていない文を台帳に残さない。
    """
    for option in result_options(text, date(2026, 11, 9)):
        said = option["result"]
        if option.get("title") == "退院の予定になった":
            assert "退院でき" in option["label"] and "なけ" not in option["label"]
        assert not ("再検査" in option["label"] and "なけ" in option["label"] and "title" in option)
        assert said.endswith("と言われた")
    titled = [o for o in result_options(text, date(2026, 11, 9)) if "title" in o]
    assert all(
        (
            "なけ" not in o["label"]
            and "悪" not in o["label"]
            and "無理" not in o["label"]
            and "のび" not in o["label"]
        )
        for o in titled
    )


def test_a_chained_clause_after_a_test_keeps_one_day_and_no_undecided() -> None:
    """「再検査なら17日、その後の退院は19日」：大きな日は17日だけ。候補が残るので未定は出さない。"""
    text = "12日に採血。退院できるなら16日が候補、再検査なら17日、その後の退院は19日"
    options = result_options(text, date(2026, 11, 9))
    second = options[1]
    assert second["title"] == "再検査になった"
    assert second["days"] == ["11/17（火）"]
    assert "19日" in second["label"]
    assert "note" not in second


@pytest.mark.parametrize(
    ("text", "titles"),
    [
        (
            "12日に採血。退院になったら16日、再検査になったら17日",
            ["退院の予定になった", "再検査になった"],
        ),
        (
            "12日に採血。退院できるなら16日、再検査になれば17日",
            ["退院の予定になった", "再検査になった"],
        ),
    ],
)
def test_affirmative_forms_of_the_kind_words(text: str, titles: list[str]) -> None:
    """肯定の「〜になったら」「〜になれば」も種類の選択肢になる。内部の印は API に出さない。"""
    options = result_options(text, date(2026, 11, 9))
    assert [option.get("title") for option in options] == titles
    assert all("chained" not in option for option in options)


SHOOT_WORDS = (
    "先生から「12日に採血。結果が良ければ16日に退院、そうでない場合は17日に再検査」と言われた。"
)


def test_tail_kind_words_make_the_figure_options() -> None:
    """2026-11-08 PO の撮影の言葉：種類は節の後ろの「◯日に退院／◯日に再検査」で決める。"""
    first, second = result_options(SHOOT_WORDS, date(2026, 11, 9))
    assert first["title"] == "退院の予定になった"
    assert first["days"] == ["11/16（月）"]
    assert first["certainty"] == "見込み"
    assert first["label"] == "結果が良ければ16日に退院"
    assert first["result"] == "16日に退院の予定になったと言われた"
    assert second["title"] == "再検査になった"
    assert second["days"] == ["11/17（火）"]
    assert second["certainty"] == "確定"
    assert second["note"] == "退院日は未定"
    assert second["result"] == "17日に再検査になったと言われた"
    assert after_title(SHOOT_WORDS) == "採血のあと"


@pytest.mark.parametrize(
    "clause",
    [
        "結果が悪ければ17日に再検査",
        "退院できなければ17日に再検査",
        "結果が良ければ16日に退院できない",
        "そうでない場合は17日に再検査かもしれない",
    ],
)
def test_tail_kind_needs_the_fixed_condition_and_ending(clause: str) -> None:
    """よい結果か「そうでない場合」の形で、節が「◯日に退院／再検査」で終わるときだけ。"""
    from ensemble.week import _tail_kind_option

    assert _tail_kind_option(clause) is None


def test_tail_kind_words_mark_the_calendar_before_a_plan() -> None:
    """案の前のカレンダー：新しい言葉でも 16日は「候補」、12日は採血、17日は再検査（dev-review の P1）。"""
    family = _doctor_family()
    family.items[0].text_original = SHOOT_WORDS
    family.items[0].recorded_at = "2026-11-09T10:00:00+09:00"
    opened = date(2026, 11, 9)
    view = week_view(family, opened, days=calendar_days(opened))
    assert view is not None
    marks = {row["date"]: row.get("mark") for row in view["rows"] if row.get("mark")}
    assert marks["11/12"] == {"text": "採血", "tone": "test"}
    assert marks["11/16"] == {"text": "候補", "tone": "likely"}
    assert marks["11/17"] == {"text": "再検査", "tone": "test"}


def test_tail_test_clause_is_not_a_declared_test_after_a_plan() -> None:
    """16日が確定の案の後、「そうでない場合は17日に再検査」の17日に印を残さない（QA の P1-18）。"""
    branch = Branch(
        id="b",
        condition_item_id="d",
        candidate_days=[CandidateDay(day="16日", certainty=Certainty.CONFIRMED)],
    )
    family = _doctor_family(branches=[branch])
    family.items[0].text_original = SHOOT_WORDS
    family.items[0].recorded_at = "2026-11-09T10:00:00+09:00"
    opened = date(2026, 11, 12)
    view = week_view(family, opened, days=calendar_days(opened))
    assert view is not None
    marks = {row["date"]: row["mark"]["text"] for row in view["rows"] if row.get("mark")}
    assert marks == {"11/12": "採血", "11/16": "確定"}
