"""図の⑩：マスの印は種類から決める。用件の語はマスに出さない。退院の未定は候補が残らないときだけ。"""

from datetime import date

from ensemble.ledger import (
    Branch,
    CandidateDay,
    Certainty,
    Family,
    Item,
    Kind,
    NextKind,
    Outcome,
    NextStep,
    Part,
    Profile,
)
from ensemble.week import calendar_days, week_view

FIGURE_WORDS = "先生から「12日に採血。退院できるなら16日が候補、再検査なら17日」と言われた。"
OLD_WORDS = (
    "先生：明日の採血しだい。よければ木曜に帰れるかも。"
    "だめなら月曜にもう一回とって、それで大丈夫なら火曜かな"
)
MONDAY = date(2026, 11, 9)
THURSDAY = date(2026, 11, 12)


def _item(item_id: str, text: str, recorded: date, **values: object) -> Item:
    fields: dict = {
        "id": item_id,
        "kind": Kind.INFO,
        "part": Part.HOSPITAL,
        "certainty": Certainty.LIKELY,
        "text_original": text,
        "recorded_at": f"{recorded.isoformat()}T15:20:00+09:00",
    }
    return Item(**{**fields, **values})


def _doctor() -> Item:
    return _item("d", FIGURE_WORDS, MONDAY, condition="採血の結果")


def _retest_branch(cancelled: bool = True) -> Branch:
    return Branch(
        id="b",
        condition_item_id="d",
        candidate_days=[CandidateDay(day="16日", certainty=Certainty.LIKELY, cancelled=cancelled)],
        outcomes=[
            Outcome(
                label="再検査になった",
                clause="再検査なら17日",
                clause_item_id="d",
                next=[NextStep(day="17日", kind=NextKind.TEST, clause="再検査なら17日")],
            )
        ],
    )


def _family(items: list[Item], branches: list[Branch] | None = None) -> Family:
    return Family(code="c" * 43, profile=Profile(name="家庭"), items=items, branches=branches or [])


def _marks(view: dict) -> dict[str, tuple[str, str]]:
    return {
        row["date"]: (row["mark"]["text"], row["mark"]["tone"])
        for row in view["rows"]
        if row["mark"]
    }


def _figure_after_retest(**draft: object) -> dict:
    result = _item("r", "17日に再検査になったと言われた", THURSDAY, refers_to="d")
    errand = _item(
        "s-b-0-0",
        "17日に再検査で、退院日は未定です。\n16日の迎えは、いったん待ってね。",
        THURSDAY,
        kind=Kind.ACTION,
        part=Part.FAMILY,
        **draft,
    )
    family = _family([_doctor(), result, errand], [_retest_branch()])
    view = week_view(family, THURSDAY, days=calendar_days(THURSDAY))
    assert view is not None
    return view


def test_figure_marks_after_the_retest() -> None:
    """12日は採血、16日は取り消しの候補に迎えが残るので見直し、17日は再検査。"""
    marks = _marks(_figure_after_retest())
    assert marks["11/12"] == ("採血", "test")
    assert marks["11/16"] == ("見直し", "review")
    assert marks["11/17"] == ("再検査", "test")
    assert all(len(text) <= 3 for text, _ in marks.values())


def test_rows_carry_the_year_and_month() -> None:
    view = _figure_after_retest()
    assert view["rows"][0]["iso"] == "2026-11-09"
    assert view["rows"][-1]["iso"] == "2026-12-06"


def test_discharge_is_undecided_only_when_no_candidate_is_left() -> None:
    assert _figure_after_retest()["discharge"] == {"undecided": True, "previous": ["11/16（月）"]}
    family = _family([_doctor()], [_retest_branch(cancelled=False)])
    view = week_view(family, THURSDAY, days=calendar_days(THURSDAY))
    assert view is not None
    assert view["discharge"] is None
    assert _marks(view)["11/16"] == ("候補", "likely")


def test_a_branch_without_candidates_says_nothing_about_discharge() -> None:
    branch = _retest_branch()
    branch.candidate_days = []
    view = week_view(_family([_doctor()], [branch]), THURSDAY, days=calendar_days(THURSDAY))
    assert view is not None
    assert view["discharge"] is None


def test_a_cancelled_day_without_errands_is_cancelled() -> None:
    family = _family([_doctor()], [_retest_branch()])
    view = week_view(family, THURSDAY, days=calendar_days(THURSDAY))
    assert view is not None
    assert _marks(view)["11/16"] == ("取消", "cancelled")


def test_a_conditional_test_is_not_marked_before_a_plan() -> None:
    """「再検査なら17日」は案の前は印にしない。言い切った「12日に採血」だけ。"""
    view = week_view(_family([_doctor()]), MONDAY, days=calendar_days(MONDAY))
    assert view is not None
    assert _marks(view) == {"11/12": ("採血", "test")}


def test_a_test_step_without_a_test_word_uses_the_kind() -> None:
    """家庭Aの「月曜にもう一回とって」は検査の語が無いので、種類の語「検査」。"""
    tuesday = date(2026, 11, 3)
    doctor = _item("d", OLD_WORDS, tuesday, condition="採血の結果")
    branch = Branch(
        id="b",
        condition_item_id="d",
        outcomes=[
            Outcome(
                label="採血がだめだった",
                clause="だめなら月曜にもう一回とって",
                clause_item_id="d",
                next=[
                    NextStep(day="月曜", kind=NextKind.TEST, clause="だめなら月曜にもう一回とって")
                ],
            )
        ],
    )
    view = week_view(_family([doctor], [branch]), tuesday, days=calendar_days(tuesday))
    assert view is not None
    marks = _marks(view)
    assert marks["11/4"] == ("採血", "test")
    assert marks["11/9"] == ("検査", "test")


def test_errand_words_are_not_marks() -> None:
    """用件（迎え・着替え）はマスの語にしない。画面が点で出す。"""
    errand = _item("e", "13日に着替えを持っていく", THURSDAY, kind=Kind.ACTION, part=Part.FAMILY)
    view = week_view(_family([_doctor(), errand]), THURSDAY, days=calendar_days(THURSDAY))
    assert view is not None
    assert "11/13" not in _marks(view)
    row = next(row for row in view["rows"] if row["date"] == "11/13")
    assert row["cells"][2][0]["item_id"] == "e"
