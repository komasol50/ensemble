"""伝え直しの流れを、偽のクライアントで確かめる。テストでは本物のAPIを呼ばない。"""

from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.engine import Engine

import ensemble.retell as retell_module
from ensemble.budget import DailyLimitReached, consume_call
from ensemble.guards import (
    CANNOT_PROPOSE_TEXT,
    check_branch,
    check_dates,
    check_restored_days,
    check_result_stage,
    check_test_days,
    extract_date_tokens,
    is_own_note,
    keep_true_quotes,
)
from ensemble.intake import build_intake_message
from ensemble.ledger import (
    Action,
    ActionType,
    Branch,
    CandidateDay,
    Certainty,
    Item,
    Kind,
    NextKind,
    NextStep,
    Outcome,
    Part,
)
from ensemble.presets import load_family_from_preset
from ensemble.retell import MissingKey, api_key, build_user_message, cost_usd, retell


def branch(draft: str = "月曜と火曜の延長に空きはありますか", basis: str = "a-07") -> Branch:
    return Branch.model_validate(
        {
            "id": "branch-1",
            "condition_item_id": "a-07",
            "candidate_days": [{"day": "木曜", "certainty": "見込み"}],
            "outcomes": [
                {
                    "label": "だめなら",
                    "actions": [
                        {
                            "part": Part.CHILDCARE,
                            "type": ActionType.CHECK_AVAILABILITY,
                            "draft": draft,
                            "basis_ids": [basis],
                        }
                    ],
                }
            ],
        }
    )


class FakeClient:
    """順番に決めた応答を返す。"""

    def __init__(self, *outputs: Branch | None) -> None:
        self.outputs = list(outputs)
        self.calls: list[dict] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(parse=self.parse))

    def parse(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(
            parsed_output=self.outputs.pop(0),
            stop_reason="end_turn",
            model=kwargs["model"],
            usage=SimpleNamespace(input_tokens=1000, output_tokens=500),
        )


@pytest.fixture(autouse=True)
def no_real_log(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        retell_module,
        "record_ai_call",
        lambda *a, **k: None,
    )


def test_good_output_passes_on_first_call() -> None:
    client = FakeClient(branch())
    family = load_family_from_preset("home_a")
    result = retell(client, family, "a-07", "condition")
    assert result.branch is not None
    assert len(client.calls) == 1
    assert client.calls[0]["model"] == "claude-opus-5-5"
    assert client.calls[0]["fallbacks"] == "default"


def test_bad_output_is_retried_once_with_the_errors() -> None:
    client = FakeClient(branch(basis="ghost"), branch())
    result = retell(client, load_family_from_preset("home_a"), "a-07", "condition")
    assert result.branch is not None
    assert len(client.calls) == 2
    second = client.calls[1]["messages"][0]["content"]
    assert "ghost" in second
    assert "検査で落ちました" in second


def test_two_failures_show_no_proposal() -> None:
    client = FakeClient(branch(draft="水曜に空きはありますか"), branch(basis="ghost"))
    result = retell(client, load_family_from_preset("home_a"), "a-07", "condition")
    assert result.branch is None
    assert result.message == CANNOT_PROPOSE_TEXT
    assert len(client.calls) == 2


def test_no_structured_output_counts_as_failure() -> None:
    client = FakeClient(None, branch())
    result = retell(client, load_family_from_preset("home_a"), "a-07", "condition")
    assert result.branch is not None
    assert len(result.attempts) == 2
    assert result.attempts[0].violations


def test_check_branch_rejects_invented_candidate_day_and_unknown_condition() -> None:
    family = load_family_from_preset("home_a")
    bad = branch()
    bad.candidate_days[0].day = "金曜"
    bad.condition_item_id = "ghost"
    violations = check_branch(bad, family)
    assert any("金曜" in v for v in violations)
    assert any("ghost" in v for v in violations)


def test_each_call_is_counted_before_it_is_made(engine: Engine) -> None:
    client = FakeClient(branch())
    with pytest.raises(DailyLimitReached):
        retell(
            client,
            load_family_from_preset("home_a"),
            "a-07",
            "condition",
            before_call=lambda: consume_call(engine, limit=0),
        )
    assert client.calls == []


def test_daily_limit_stops_at_the_limit(engine: Engine) -> None:
    assert consume_call(engine, limit=2) == 1
    assert consume_call(engine, limit=2) == 2
    with pytest.raises(DailyLimitReached):
        consume_call(engine, limit=2)
    assert consume_call(engine, scope="another", limit=2) == 1


def test_family_code_is_not_sent_to_the_model() -> None:
    family = load_family_from_preset("home_a")
    assert family.code not in build_user_message(family, "a-07", "condition")


def test_cost_estimate() -> None:
    assert cost_usd({"input_tokens": 1_000_000, "output_tokens": 0}) == 4.0
    assert cost_usd({"input_tokens": 0, "output_tokens": 1_000_000}) == 20.0


def test_key_comes_only_from_dotenv_locally(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(retell_module, "ROOT", tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ambient-key-for-another-purpose")
    monkeypatch.delenv("RENDER", raising=False)
    with pytest.raises(MissingKey):
        api_key()

    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=your_key_here\n", encoding="utf-8")
    with pytest.raises(MissingKey):
        api_key()

    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=key-from-dotenv\n", encoding="utf-8")
    assert api_key() == "key-from-dotenv"


def test_key_comes_from_environment_on_render(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(retell_module, "ROOT", tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "render-key")
    monkeypatch.setenv("RENDER", "true")
    assert api_key() == "render-key"


def test_request_before_the_result_is_stopped_even_with_a_confirmed_basis() -> None:
    """無関係な確定の項目（a-03）を根拠に混ぜても、結果の前は頼めない。"""
    bad = branch()
    action = bad.outcomes[0].actions[0]
    action.part = Part.FAMILY
    action.type = ActionType.REQUEST
    action.basis_ids = ["a-03", "a-07"]
    client = FakeClient(bad, bad)
    result = retell(client, load_family_from_preset("home_a"), "a-07", "condition")
    assert result.branch is None and len(client.calls) == 2


def test_request_after_the_result_is_allowed() -> None:
    good = branch()
    action = good.outcomes[0].actions[0]
    action.part = Part.FAMILY
    action.type = ActionType.REQUEST
    action.basis_ids = ["a-03", "a-07"]
    family = load_family_from_preset("home_a")
    confirmed = next(item for item in family.items if item.certainty == Certainty.CONFIRMED)
    result = retell(FakeClient(good), family, confirmed.id, "result")
    assert result.branch is not None


def test_request_needs_the_result_itself_to_be_confirmed() -> None:
    """無関係な確定の項目が根拠にあるだけでは、頼む理由にならない。"""
    good = branch()
    action = good.outcomes[0].actions[0]
    action.part = Part.FAMILY
    action.type = ActionType.REQUEST
    action.basis_ids = ["a-03", "a-07"]
    result = retell(FakeClient(good, good), load_family_from_preset("home_a"), "a-07", "result")
    assert result.branch is None
    assert any("確定していない" in v for v in result.attempts[0].violations)


def test_candidate_day_must_come_from_the_condition_not_the_whole_ledger() -> None:
    """別の項目にある曜日では、退院の候補日にならない。"""
    family = load_family_from_preset("home_a")
    bad = branch()
    doctor = next(item for item in family.items if item.id == "a-07").text_original
    other_day = next(
        token
        for item in family.items
        if item.id != "a-07" and item.text_original
        for token in extract_date_tokens(item.text_original)
        if token.endswith("曜") and token not in extract_date_tokens(doctor)
    )
    bad.candidate_days[0].day = other_day
    assert any(other_day in v for v in check_branch(bad, family))


def test_label_is_checked_for_dates_and_medical_words() -> None:
    family = load_family_from_preset("home_a")
    bad = branch()
    bad.outcomes[0].label = "金曜に薬を増やすなら"
    violations = check_branch(bad, family)
    assert any("見出し" in v and "金曜" in v for v in violations)
    assert any("見出し" in v and "医療の語" in v for v in violations)


def test_family_name_is_not_written_in_a_draft() -> None:
    family = load_family_from_preset("home_a")
    bad = branch(draft="家庭Aです。月曜と火曜の延長に空きはありますか")
    assert any("呼び名" in v for v in check_branch(bad, family))


def test_own_note_is_not_sent_to_the_ai() -> None:
    """親の一言は連絡の材料にしない。夫や職場への文に混ざらないように。"""
    family = load_family_from_preset("home_a")
    family.items.append(
        Item(
            id="n1",
            kind=Kind.INFO,
            part=Part.PARENT,
            certainty=Certainty.LIKELY,
            speaker="親",
            text_original="今日はパンだけ。少し眠れた",
        )
    )
    assert "パンだけ" not in build_user_message(family, "a-07", "condition")
    assert "n1" not in build_intake_message(family, "着替えをお願い")


def test_greeting_goes_first_in_the_prompt() -> None:
    assert "文の先頭に一言だけ" in retell_module.SYSTEM_PROMPT
    assert "用件の後ろには書きません" in retell_module.SYSTEM_PROMPT


def test_work_rules_are_in_the_prompt() -> None:
    for rule in (
        "候補日と条件",
        "代役か延期か",
        "子の入院に付き添う",
        "扱いはご相談させてください",
    ):
        assert rule in retell_module.SYSTEM_PROMPT


def test_todays_date_does_not_pass_the_date_guard() -> None:
    """プロンプトに渡す今日の日付は、下書きの日付の根拠にならない（Plan main の条件1）。"""
    family = load_family_from_preset("home_a")
    message = build_user_message(family, "a-07", "condition", today=date(2026, 11, 5))
    assert "today: 2026-11-05（木）" in message
    assert check_dates("11/5に伺います", family.items) != []


def test_context_has_earlier_plans_and_replies_but_not_own_notes() -> None:
    family = load_family_from_preset("home_a")
    own = (
        next(item for item in family.items if is_own_note(item))
        if any(is_own_note(item) for item in family.items)
        else None
    )
    family.branches.append(
        Branch(
            id="b-old",
            condition_item_id="a-07",
            candidate_days=[CandidateDay(day="木曜", certainty=Certainty.LIKELY, cancelled=True)],
            outcomes=[],
        )
    )
    history = {
        "a-02": [{"at": "2026-11-04T19:00:00+09:00", "label": "夫：引き受けた"}],
        "a-03": [{"at": "2026-11-04T19:00:00+09:00", "label": "直した"}],
    }
    if own:
        history[own.id] = [{"at": "2026-11-04T19:00:00+09:00", "label": "見えてはいけない"}]
    message = build_user_message(family, "a-07", "condition", history=history)
    assert '"b-old"' in message and '"cancelled": true' in message
    assert "夫：引き受けた" in message
    assert "直した" not in message and "見えてはいけない" not in message


def test_parents_choice_is_passed_as_a_choice() -> None:
    family = load_family_from_preset("home_a")
    message = build_user_message(family, "a-07", "result", chosen="だめなら月曜にもう一回とって")
    assert "parent_chose: だめなら月曜にもう一回とって" in message


def _quoted_branch(**action: object) -> Branch:
    return Branch(
        id="b",
        condition_item_id="a-07",
        outcomes=[
            Outcome(
                label="だめ",
                clause="だめなら月曜にもう一回とって",
                clause_item_id="a-07",
                next=[
                    NextStep(day="月曜", kind=NextKind.TEST, clause="だめなら月曜にもう一回とって"),
                    NextStep(
                        day="火曜", kind=NextKind.DISCHARGE, clause="それで大丈夫なら火曜かな"
                    ),
                ],
                actions=[
                    Action(
                        part=Part.FAMILY,
                        type=ActionType.CONFIRM,
                        draft="着替えを持ってきてほしい。夜で大丈夫。",
                        **action,
                    )
                ],
            )
        ],
    )


def test_quotes_must_be_in_the_original() -> None:
    family = load_family_from_preset("home_a")
    kept = keep_true_quotes(_quoted_branch(ask="着替えを持ってきてほしい。"), family)
    outcome = kept.outcomes[0]
    assert outcome.clause == "だめなら月曜にもう一回とって"
    assert [step.day for step in outcome.next] == ["月曜", "火曜"]
    assert outcome.actions[0].ask == "着替えを持ってきてほしい。"


def test_next_steps_are_all_or_nothing() -> None:
    family = load_family_from_preset("home_a")
    branch = _quoted_branch()
    # 同じ節に無い日が1つあれば、片方だけを残さず全部捨てる。
    branch.outcomes[0].next[1] = NextStep(
        day="土曜", kind=NextKind.DISCHARGE, clause="それで大丈夫なら火曜かな"
    )
    assert keep_true_quotes(branch, family).outcomes[0].next == []


def test_quotes_fall_back_when_not_found() -> None:
    family = load_family_from_preset("home_a")
    branch = _quoted_branch(ask="下書きに無い文")
    branch.outcomes[0].clause = "原文に無い言葉"
    kept = keep_true_quotes(branch, family)
    assert kept.outcomes[0].clause is None
    assert kept.outcomes[0].actions[0].ask is None


def test_quotes_come_only_from_a_hospital_item() -> None:
    """親の入れた文や親が直した文を、医師の言葉として引用しない。"""
    family = load_family_from_preset("home_a")
    family.items.append(
        Item(
            id="p-1",
            kind=Kind.INFO,
            part=Part.FAMILY,
            certainty=Certainty.CONFIRMED,
            text_original="だめなら月曜にもう一回とって",
        )
    )
    branch = _quoted_branch()
    branch.outcomes[0].clause_item_id = "p-1"
    kept = keep_true_quotes(branch, family)
    assert kept.outcomes[0].clause is None and kept.outcomes[0].next == []


def test_a_cancelled_day_cannot_come_back() -> None:
    family = load_family_from_preset("home_a")
    family.branches.append(
        Branch(
            id="b-old",
            condition_item_id="a-07",
            candidate_days=[CandidateDay(day="木曜", certainty=Certainty.LIKELY, cancelled=True)],
        )
    )
    new = Branch(
        id="b",
        condition_item_id="a-07",
        candidate_days=[CandidateDay(day="木曜", certainty=Certainty.LIKELY)],
    )
    assert any("取り消した日" in violation for violation in check_restored_days(new, family))
    new.candidate_days[0].cancelled = True
    assert check_restored_days(new, family) == []


def test_a_test_day_is_not_a_candidate_day() -> None:
    branch = _quoted_branch()
    branch.candidate_days = [CandidateDay(day="月曜", certainty=Certainty.LIKELY)]
    assert any("検査の日" in violation for violation in check_test_days(branch))
    branch.candidate_days[0].cancelled = True
    assert check_test_days(branch) == []


def test_a_result_needs_actions() -> None:
    empty = Branch(id="b", condition_item_id="a-07", outcomes=[Outcome(label="だめ")])
    trigger = Item(
        id="r",
        kind=Kind.INFO,
        part=Part.HOSPITAL,
        certainty=Certainty.CONFIRMED,
        text_original="月曜にもう一回とると言われた",
    )
    assert any("案がありません" in v for v in check_result_stage(empty, trigger))
