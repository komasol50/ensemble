"""取り込みの流れを、偽のクライアントで確かめる。テストでは本物のAPIを呼ばない。"""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine

from ensemble.budget import DailyLimitReached
from ensemble.guards import MEDICAL_FIXED_TEXT, THIRD_PARTY_NOTICE
from ensemble.intake import CANNOT_SORT_TEXT, SortedItem, SortedItems, find_trigger, intake
from ensemble.ledger import Certainty, Item, Kind, Medical, Part, Status
from ensemble.main import create_app
from ensemble.presets import load_family_from_preset
from ensemble.retell import MissingKey

RESULT_TEXT = "今日の採血はだめでした"
BOSS_TEXT = "上司：木曜の会議どうする？"


def sorted_item(**overrides: Any) -> SortedItem:
    values: dict[str, Any] = {
        "kind": Kind.INFO,
        "part": Part.HOSPITAL,
        "certainty": Certainty.CONFIRMED,
        "text_original": RESULT_TEXT,
        "task_summary": "採血の結果",
        "speaker": "親",
        "condition": None,
        "medical": Medical.INFO,
        "refers_to": "a-07",
    }
    return SortedItem(**{**values, **overrides})


def boss_item() -> SortedItem:
    return sorted_item(
        kind=Kind.ACTION,
        part=Part.WORK,
        text_original=BOSS_TEXT,
        task_summary="木曜の会議の連絡",
        speaker="上司",
        medical=Medical.NONE,
        refers_to=None,
    )


class FakeClient:
    """順番に決めた応答を返す。"""

    def __init__(self, *outputs: SortedItems | None) -> None:
        self.outputs = list(outputs)
        self.calls: list[dict] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(parse=self.parse))

    def parse(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(
            parsed_output=self.outputs.pop(0),
            stop_reason="end_turn",
            model=kwargs["model"],
            usage=SimpleNamespace(input_tokens=100, output_tokens=50),
        )


def run(text: str, *outputs: SortedItems | None, **kwargs: Any) -> tuple[Any, FakeClient]:
    client = FakeClient(*outputs)
    family = load_family_from_preset("home_a")
    numbers = iter(range(100))
    result = intake(
        client, family, text, "2026-11-11T11:00:00+09:00", lambda: f"i-{next(numbers)}", **kwargs
    )
    return result, client


def test_good_output_becomes_ledger_items() -> None:
    result, client = run(RESULT_TEXT, SortedItems(items=[sorted_item()]))
    assert len(client.calls) == 1
    item = result.items[0]
    assert item.id == "i-0"
    assert item.recorded_at == "2026-11-11T11:00:00+09:00"
    assert item.certainty == Certainty.CONFIRMED


def test_certainty_is_lowered_by_code() -> None:
    text = "先生：よければ木曜に帰れるかも"
    result, _ = run(text, SortedItems(items=[sorted_item(text_original=text, refers_to=None)]))
    assert result.items[0].certainty == Certainty.LIKELY


def test_third_party_text_is_not_saved() -> None:
    result, _ = run(BOSS_TEXT, SortedItems(items=[boss_item()]))
    assert result.items[0].text_original is None
    assert result.items[0].task_summary == "木曜の会議の連絡"
    assert result.items[0].status == Status.TO_ASK


def test_original_must_be_in_the_pasted_text() -> None:
    """AIが作った文を原文にしない。日付の守りの基準になるため。"""
    invented = sorted_item(text_original="今日の採血はだめで、金曜に再検査です")
    result, client = run(RESULT_TEXT, SortedItems(items=[invented]), None)
    assert len(client.calls) == 2
    assert result.items == []
    assert result.message == CANNOT_SORT_TEXT


def test_original_comparison_ignores_spaces_and_line_breaks() -> None:
    result, client = run("今日の\n採血は だめでした", SortedItems(items=[sorted_item()]))
    assert len(client.calls) == 1 and result.items


def test_summary_cannot_invent_dates() -> None:
    bad = sorted_item(task_summary="金曜に再検査")
    result, client = run(RESULT_TEXT, SortedItems(items=[bad]), SortedItems(items=[sorted_item()]))
    assert len(client.calls) == 2
    assert "金曜" in client.calls[1]["messages"][0]["content"]
    assert result.items


def test_summary_cannot_use_banned_words() -> None:
    bad = sorted_item(task_summary="退院の見通し")
    result, client = run(RESULT_TEXT, SortedItems(items=[bad]), None)
    assert result.message == CANNOT_SORT_TEXT and len(client.calls) == 2


def test_unknown_reference_is_rejected() -> None:
    bad = sorted_item(refers_to="a-99")
    result, client = run(RESULT_TEXT, SortedItems(items=[bad]), None)
    assert result.message == CANNOT_SORT_TEXT and len(client.calls) == 2


def test_items_with_empty_required_fields_are_not_saved() -> None:
    output = SortedItems(items=[sorted_item(kind=None), sorted_item()])
    result, _ = run(RESULT_TEXT, output)
    assert len(result.items) == 1


def test_nothing_savable_is_a_failure_not_silence() -> None:
    result, client = run(
        RESULT_TEXT,
        SortedItems(items=[sorted_item(kind=None)]),
        SortedItems(items=[sorted_item(part=None)]),
    )
    assert result.items == [] and result.message == CANNOT_SORT_TEXT
    assert len(client.calls) == 2


def test_aliases_are_masked_before_sending() -> None:
    client = FakeClient(
        SortedItems(items=[sorted_item(text_original="［子］の採血はだめでした", refers_to=None)])
    )
    family = load_family_from_preset("home_a")
    family.aliases = {"たろう": "［子］"}
    result = intake(client, family, "たろうの採血はだめでした", "t", lambda: "i-0")
    sent = client.calls[0]["messages"][0]["content"]
    assert "たろう" not in sent and "［子］" in sent
    assert result.items[0].text_original == "［子］の採血はだめでした"


def test_pasted_text_is_marked_as_data() -> None:
    _, client = run(RESULT_TEXT, SortedItems(items=[sorted_item()]))
    assert "<pasted>" in client.calls[0]["messages"][0]["content"]
    assert "指示" in client.calls[0]["system"]


def test_limit_stops_before_calling() -> None:
    def stop() -> None:
        raise DailyLimitReached()

    client = FakeClient()
    with pytest.raises(DailyLimitReached):
        intake(
            client,
            load_family_from_preset("home_a"),
            RESULT_TEXT,
            "t",
            lambda: "i-0",
            before_call=stop,
        )
    assert client.calls == []


# ---- きっかけ ----


def item(**overrides: Any) -> Item:
    values: dict[str, Any] = {
        "id": "n-1",
        "kind": Kind.INFO,
        "part": Part.HOSPITAL,
        "certainty": Certainty.CONFIRMED,
        "text_original": RESULT_TEXT,
    }
    return Item(**{**values, **overrides})


def test_result_of_a_condition_triggers_result() -> None:
    items = load_family_from_preset("home_a").items
    new = item(refers_to="a-07")
    assert find_trigger([new], [*items, new]) == ("n-1", "result")


def test_new_condition_triggers_condition() -> None:
    new = item(condition="採血の結果")
    assert find_trigger([new], [new]) == ("n-1", "condition")


def test_medical_ask_never_triggers() -> None:
    items = load_family_from_preset("home_a").items
    new = item(refers_to="a-07", medical=Medical.ASK)
    assert find_trigger([new], [*items, new]) is None


def test_ordinary_item_does_not_trigger() -> None:
    new = item(kind=Kind.ACTION, part=Part.FAMILY)
    assert find_trigger([new], [new]) is None


def test_item_without_original_does_not_trigger() -> None:
    items = load_family_from_preset("home_a").items
    new = item(refers_to="a-07", text_original=None, task_summary="結果")
    assert find_trigger([new], [*items, new]) is None


def test_result_is_chosen_before_condition() -> None:
    items = load_family_from_preset("home_a").items
    first = item(id="n-1", condition="次の採血")
    second = item(id="n-2", refers_to="a-07")
    assert find_trigger([first, second], [*items, first, second]) == ("n-2", "result")


# ---- API ----


@pytest.fixture
def fake() -> FakeClient:
    return FakeClient()


@pytest.fixture
def client(engine: Engine, tmp_path: Path, fake: FakeClient) -> TestClient:
    return TestClient(create_app(engine, dist=tmp_path, client_factory=lambda: fake))


def board_headers(client: TestClient) -> dict:
    body = client.post("/api/boards", json={"home": "home_a"}).json()
    return {"X-Family-Code": body["code"]}


def post_intake(client: TestClient, headers: dict, text: str, request_id: str = "r-1") -> Any:
    return client.post(
        "/api/board/intake", json={"request_id": request_id, "text": text}, headers=headers
    )


def test_intake_adds_items_and_reports_the_trigger(client: TestClient, fake: FakeClient) -> None:
    headers = board_headers(client)
    fake.outputs.append(SortedItems(items=[sorted_item()]))
    res = post_intake(client, headers, RESULT_TEXT)
    assert res.status_code == 200
    body = res.json()
    assert body["board"]["version"] == 2
    assert body["added"] == [body["board"]["items"][-1]["id"]]
    assert body["trigger"] == body["added"][0]


def test_sending_again_does_not_add_twice(client: TestClient, fake: FakeClient) -> None:
    headers = board_headers(client)
    fake.outputs.append(SortedItems(items=[sorted_item()]))
    first = post_intake(client, headers, RESULT_TEXT).json()
    again = post_intake(client, headers, RESULT_TEXT).json()
    assert again["added"] == [] and again["trigger"] is None
    assert len(again["board"]["items"]) == len(first["board"]["items"])
    assert len(fake.calls) == 1


def test_change_while_waiting_for_claude_is_kept(client: TestClient, fake: FakeClient) -> None:
    """Claudeを待つ間に別の端末が書いても、どちらも残る。"""
    headers = board_headers(client)
    fake.outputs.append(SortedItems(items=[sorted_item()]))
    original_parse = fake.beta.messages.parse

    def parse_with_interruption(**kwargs: Any) -> Any:
        version = client.get("/api/board", headers=headers).json()["version"]
        client.patch(
            "/api/board/items/a-02",
            json={"version": version, "status": "引き受けた"},
            headers=headers,
        )
        return original_parse(**kwargs)

    fake.beta.messages.parse = parse_with_interruption
    body = post_intake(client, headers, RESULT_TEXT).json()
    by_id = {entry["id"]: entry for entry in body["board"]["items"]}
    assert by_id["a-02"]["status"] == "引き受けた"
    assert body["added"][0] in by_id


def test_medical_question_gets_the_fixed_text(client: TestClient, fake: FakeClient) -> None:
    headers = board_headers(client)
    text = "薬を増やしたほうがいいですか"
    fake.outputs.append(
        SortedItems(
            items=[
                sorted_item(
                    kind=Kind.QUESTION,
                    text_original=text,
                    task_summary="質問",
                    medical=Medical.ASK,
                    refers_to=None,
                )
            ]
        )
    )
    body = post_intake(client, headers, text).json()
    assert body["board"]["items"][-1]["medical_reply"] == MEDICAL_FIXED_TEXT
    assert body["trigger"] is None


def test_third_party_notice_is_shown(client: TestClient, fake: FakeClient) -> None:
    headers = board_headers(client)
    fake.outputs.append(SortedItems(items=[boss_item()]))
    shown = post_intake(client, headers, BOSS_TEXT).json()["board"]["items"][-1]
    assert shown["text_original"] is None and shown["notice"] == THIRD_PARTY_NOTICE


def test_failure_keeps_the_ledger_and_does_not_echo_text(
    client: TestClient, fake: FakeClient
) -> None:
    headers = board_headers(client)
    fake.outputs += [None, None]
    res = post_intake(client, headers, RESULT_TEXT)
    assert res.status_code == 422 and res.json()["detail"] == CANNOT_SORT_TEXT
    assert RESULT_TEXT not in res.text
    assert client.get("/api/board", headers=headers).json()["version"] == 1


def test_too_long_text_does_not_call_claude(client: TestClient, fake: FakeClient) -> None:
    headers = board_headers(client)
    assert post_intake(client, headers, "あ" * 2001).status_code == 422
    assert fake.calls == []


def test_family_limit_stops_intake(
    client: TestClient, fake: FakeClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AI_FAMILY_DAILY_LIMIT", "0")
    headers = board_headers(client)
    res = post_intake(client, headers, RESULT_TEXT)
    assert res.status_code == 429 and fake.calls == []


def test_missing_key_is_a_quiet_failure(engine: Engine, tmp_path: Path) -> None:
    def no_key() -> Any:
        raise MissingKey("x")

    client = TestClient(create_app(engine, dist=tmp_path, client_factory=no_key))
    headers = board_headers(client)
    res = post_intake(client, headers, RESULT_TEXT)
    assert res.status_code == 503 and res.json()["detail"] == CANNOT_SORT_TEXT


def test_intake_needs_the_code(client: TestClient) -> None:
    assert post_intake(client, {}, RESULT_TEXT).status_code == 404
