"""図（新UI_2026-11-05）の筋書きに揃えたところの試験。"""

from ensemble.api import carried_item_ids, superseded_item_ids
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


def _sent(branch_id: str = "b-1") -> Item:
    """案から作った送る文（夫への1通）。"""
    return Item(
        id=f"s-{branch_id}-0-0",
        kind=Kind.ACTION,
        part=Part.FAMILY,
        certainty=Certainty.CONFIRMED,
        text_original="寝室の棚の着替えを持ってきてもらえる？",
        speaker="親",
        status=Status.TO_ASK,
        recorded_at="2026-11-12T16:05:00+09:00",
    )


def test_a_request_carried_into_the_later_draft_is_not_sent_twice() -> None:
    """夫への文が「着替えも頼みたい」を根拠に引き継いだら、親の前の文は済の扱い（送る一覧に出ない）。"""
    family = load_family_from_preset("shoot_a")
    family.items += [_request(), _sent()]
    family.branches.append(
        _branch(
            Action(
                part=Part.FAMILY,
                type=ActionType.REQUEST,
                draft="寝室の棚の着替えを持ってきてもらえる？",
                basis_ids=["i-request", "a-02"],
            )
        )
    )
    carried = carried_item_ids(family)
    assert carried["i-request"] == "s-b-1-0-0"
    # 夫の「今夜、何を持っていく？」への答えも同じ文に入っている。
    assert carried["a-02"] == "s-b-1-0-0"
    assert "i-request" not in superseded_item_ids(family)


def test_deleting_the_draft_brings_the_request_back() -> None:
    """親が夫への下書きを消したら、引き継ぎは無くなり、着替えの頼みごとは元のまま送れる（QA Test の P1-1）。"""
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
    assert carried_item_ids(family) == {}
    assert "i-request" not in superseded_item_ids(family)


def test_a_request_stays_when_another_person_cites_it() -> None:
    """別の相手への文が根拠に挙げても、夫への頼みごとはそのまま送れる。"""
    family = load_family_from_preset("shoot_a")
    sent = _sent()
    sent.part = Part.WORK
    family.items += [_request(), sent]
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
    assert carried_item_ids(family) == {}


def test_shoot_b_starts_with_the_figure_doctor_words() -> None:
    """家庭Bの撮影は 11/12 の「採血のあと」から撮る。医師の言葉は入れてあり、⑤の3択が出る。"""
    from datetime import date

    from ensemble.week import doctor_view

    family = load_family_from_preset("shoot_b")
    view = doctor_view(family, date(2026, 11, 12))
    assert view is not None
    assert [option.get("title") for option in view["options"]] == [
        "退院の予定になった",
        "再検査になった",
    ]
    assert view["after_title"] == "採血のあと"
    # 夫に平日の迎えを頼めない家庭。支援（ファミサポ）が登録されている。
    assert any("ファミサポ" in support.name for support in family.profile.supports)


def test_free_search_without_documents_still_returns_the_fixed_text() -> None:
    """④：資料が0枚でも、医療の問いには固定文を返す。資料の無い生活の問いは探せない（422）。"""
    import os
    import tempfile
    from pathlib import Path

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine

    from ensemble.db import init_db
    from ensemble.guards import MEDICAL_FIXED_TEXT
    from ensemble.main import create_app

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        engine = create_engine(f"sqlite:///{(Path(tmp) / 'x.db').as_posix()}")
        init_db(engine)
        os.environ.pop("ANTHROPIC_API_KEY", None)
        client = TestClient(create_app(engine, dist=Path(tmp)))
        code = client.post("/api/boards", json={"home": "home_a"}).json()["code"]
        headers = {"X-Family-Code": code}
        medical = client.post(
            "/api/board/search",
            json={"question": "この薬、減らしてもいい？", "docs": []},
            headers=headers,
        )
        assert medical.status_code == 200
        assert medical.json()["medical"] == MEDICAL_FIXED_TEXT
        life = client.post(
            "/api/board/search", json={"question": "面会の時間は？", "docs": []}, headers=headers
        )
        assert life.status_code == 422
        engine.dispose()


def test_calendar_marks_the_figure_days_before_the_result() -> None:
    """図の⑩（11/12、結果の前）：12日は採血、16日は候補、17日は再検査。"""
    from datetime import date

    from ensemble.week import calendar_days, week_view

    family = load_family_from_preset("shoot_b")
    today = date(2026, 11, 12)
    view = week_view(family, today, days=calendar_days(today))
    marks = {row["date"]: (row["mark"] or {}).get("text") for row in view["rows"]}
    assert marks["11/12"] == "採血"
    assert marks["11/16"] == "候補"
    assert marks["11/17"] == "再検査"


def test_unset_contacts_are_not_stored() -> None:
    """連絡する相手が未設定の台帳は、その欄を保存しない（前の版へ戻しても開ける）。"""
    from ensemble.store import stored

    family = load_family_from_preset("home_a")
    assert family.profile.contacts is None
    assert "contacts" not in stored(family)["profile"]
    family.profile.contacts = [Part.FAMILY]
    assert stored(family)["profile"]["contacts"] == ["家族"]
