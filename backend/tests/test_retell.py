"""伝え直しの流れを、偽のクライアントで確かめる。テストでは本物のAPIを呼ばない。"""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.engine import Engine

import ensemble.retell as retell_module
from ensemble.budget import DailyLimitReached, consume_call
from ensemble.guards import CANNOT_PROPOSE_TEXT, check_branch
from ensemble.ledger import ActionType, Branch, Part
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
    monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)
    with pytest.raises(MissingKey):
        api_key()

    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=your_key_here\n", encoding="utf-8")
    with pytest.raises(MissingKey):
        api_key()

    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=key-from-dotenv\n", encoding="utf-8")
    assert api_key() == "key-from-dotenv"


def test_key_comes_from_environment_on_railway(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(retell_module, "ROOT", tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "railway-key")
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    assert api_key() == "railway-key"


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
    result = retell(FakeClient(good), load_family_from_preset("home_a"), "a-07", "result")
    assert result.branch is not None
