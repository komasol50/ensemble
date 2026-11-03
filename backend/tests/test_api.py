"""保存のAPI。合格の条件：古い版からの更新が失敗する／家族コードがサーバーの記録に出ない。"""

import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import Engine

import ensemble.api as api
from ensemble.budget import DailyLimitReached, consume_family_call, family_scope
from ensemble.db import ai_call_counts
from ensemble.guards import MEDICAL_FIXED_TEXT, THIRD_PARTY_NOTICE
from ensemble.ledger import Action, ActionType, Branch, Family, Outcome, Part
from ensemble.main import create_app
from ensemble.store import load_family, save_family


@pytest.fixture
def client(engine: Engine, tmp_path: Path) -> TestClient:
    return TestClient(create_app(engine, dist=tmp_path))


def make_board(client: TestClient, home: str = "home_a") -> tuple[dict, dict]:
    res = client.post("/api/boards", json={"home": home})
    assert res.status_code == 201
    body = res.json()
    return {"X-Family-Code": body["code"]}, body["board"]


def test_create_and_read_board(client: TestClient) -> None:
    headers, board = make_board(client)
    assert board["version"] == 1
    assert "code" not in board
    assert client.get("/api/board", headers=headers).json() == board
    assert [i["id"] for i in board["items"]][:2] == ["a-01", "a-02"]


def test_unknown_home_and_extra_fields_are_rejected(client: TestClient) -> None:
    assert client.post("/api/boards", json={"home": "home_c"}).status_code == 422
    assert client.post("/api/boards", json={"home": "home_a", "x": 1}).status_code == 422


def test_board_needs_the_code(client: TestClient) -> None:
    assert client.get("/api/board").status_code == 404
    assert client.get("/api/board", headers={"X-Family-Code": "nope"}).status_code == 404


def test_each_board_gets_its_own_code(client: TestClient) -> None:
    first, _ = make_board(client)
    second, _ = make_board(client)
    assert first != second


def test_view_carries_labels_and_notices(client: TestClient) -> None:
    _, board = make_board(client)
    items = {i["id"]: i for i in board["items"]}
    assert items["a-02"]["status_label"] == "未依頼"
    assert items["a-05"]["status_label"] == "返答待ち"
    assert items["a-07"]["status_label"] is None
    assert items["a-06"]["notice"] == THIRD_PARTY_NOTICE
    assert items["a-07"]["medical_reply"] is None


def test_medical_ask_gets_the_fixed_text(client: TestClient) -> None:
    headers, board = make_board(client)
    res = client.post(
        "/api/board/items",
        headers=headers,
        json={
            "version": board["version"],
            "kind": "質問",
            "part": "病院",
            "certainty": "見込み",
            "text_original": "薬を増やしたほうがいいですか",
            "speaker": "親",
            "medical": "ask",
            "status": "未確認",
        },
    )
    assert res.status_code == 201
    assert res.json()["items"][-1]["medical_reply"] == MEDICAL_FIXED_TEXT


def test_change_status_advances_the_version(client: TestClient) -> None:
    headers, _ = make_board(client)
    res = client.patch(
        "/api/board/items/a-02", headers=headers, json={"version": 1, "status": "頼んだ"}
    )
    assert res.status_code == 200
    changed = res.json()
    assert changed["version"] == 2
    assert next(i for i in changed["items"] if i["id"] == "a-02")["status_label"] == "返答待ち"


def test_stale_version_fails_and_changes_nothing(client: TestClient) -> None:
    headers, _ = make_board(client)
    client.patch("/api/board/items/a-02", headers=headers, json={"version": 1, "status": "頼んだ"})
    before = client.get("/api/board", headers=headers).json()

    stale = client.patch(
        "/api/board/items/a-02", headers=headers, json={"version": 1, "status": "完了"}
    )
    assert stale.status_code == 409
    item = {
        "version": 1,
        "kind": "行動",
        "part": "家族",
        "certainty": "見込み",
        "text_original": "x",
    }
    assert client.post("/api/board/items", headers=headers, json=item).status_code == 409
    removed = client.delete("/api/board/items/a-01", headers=headers, params={"version": 1})
    assert removed.status_code == 409
    assert client.get("/api/board", headers=headers).json() == before


def test_lost_race_between_read_and_write_is_a_conflict(
    client: TestClient, engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """読んだ後に別の端末が書いても、後の書き込みは相手を消さずに失敗する。"""
    headers, _ = make_board(client)
    code = headers["X-Family-Code"]
    real_load = api.load_family

    def load_then_other_device_writes(eng: Engine, c: str) -> Family:
        family = real_load(eng, c)
        other = real_load(eng, c)
        other.profile.name = "別の端末"
        save_family(eng, other, other.version)
        return family

    monkeypatch.setattr(api, "load_family", load_then_other_device_writes)
    res = client.patch(
        "/api/board/items/a-02", headers=headers, json={"version": 1, "status": "頼んだ"}
    )
    assert res.status_code == 409
    saved = load_family(engine, code)
    assert saved.profile.name == "別の端末"
    assert next(i for i in saved.items if i.id == "a-02").status == "頼みたい"


@pytest.mark.parametrize(
    ("item_id", "status"),
    [("a-07", "完了"), ("a-02", "答えあり"), ("a-01", "完了"), ("a-02", "ない状態")],
)
def test_status_must_match_the_kind(client: TestClient, item_id: str, status: str) -> None:
    headers, _ = make_board(client)
    res = client.patch(
        f"/api/board/items/{item_id}", headers=headers, json={"version": 1, "status": status}
    )
    assert res.status_code == 422


def test_time_status_needs_a_time(client: TestClient) -> None:
    headers, _ = make_board(client)
    body = {"version": 1, "status": "時刻つきで可"}
    assert client.patch("/api/board/items/a-02", headers=headers, json=body).status_code == 422
    body["due"] = "18:30"
    res = client.patch("/api/board/items/a-02", headers=headers, json=body)
    item = next(i for i in res.json()["items"] if i["id"] == "a-02")
    assert item["status_label"] == "18:30なら"


def test_added_item_goes_through_the_guards(client: TestClient) -> None:
    headers, _ = make_board(client)
    res = client.post(
        "/api/board/items",
        headers=headers,
        json={
            "version": 1,
            "kind": "情報",
            "part": "職場",
            "certainty": "確定",
            "speaker": "上司",
            "text_original": "木曜の会議に出られるかもしれないと聞いた",
            "task_summary": "木曜の会議の件",
        },
    )
    assert res.status_code == 201
    item = res.json()["items"][-1]
    assert item["text_original"] is None  # 第三者の文は残さない
    assert item["certainty"] == "見込み"  # 条件の言葉で下がる
    assert item["id"].startswith("i-")


@pytest.mark.parametrize(
    "change",
    [
        {"kind": None},
        {"part": ""},
        {"certainty": None},
        {"id": "a-99"},
        {"text_original": None},
        {"text_original": "あ" * 2001},
        {"status": "完了", "kind": "情報"},
        {"part": "職場", "speaker": "上司", "task_summary": None},
    ],
)
def test_bad_items_are_not_saved(client: TestClient, change: dict) -> None:
    headers, _ = make_board(client)
    body = {
        "version": 1,
        "kind": "行動",
        "part": "家族",
        "certainty": "見込み",
        "text_original": "持ってきて",
        **change,
    }
    assert client.post("/api/board/items", headers=headers, json=body).status_code == 422
    assert client.get("/api/board", headers=headers).json()["version"] == 1


def test_remove_item(client: TestClient) -> None:
    headers, _ = make_board(client)
    res = client.delete("/api/board/items/a-04", headers=headers, params={"version": 1})
    assert res.status_code == 200
    assert "a-04" not in [i["id"] for i in res.json()["items"]]
    again = client.delete("/api/board/items/a-04", headers=headers, params={"version": 2})
    assert again.status_code == 404


def test_code_never_appears_in_urls_logs_or_board(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    headers, _ = make_board(client)
    code = headers["X-Family-Code"]
    seen: list[str] = []
    client.event_hooks["request"] = [lambda request: seen.append(str(request.url))]
    client.get("/api/board", headers=headers)
    client.patch("/api/board/items/a-02", headers=headers, json={"version": 1, "status": "頼んだ"})
    client.delete("/api/board/items/a-04", headers=headers, params={"version": 2})
    assert len(seen) == 3
    assert all(code not in url for url in seen)
    assert code not in caplog.text
    assert code not in client.get("/api/board", headers=headers).text


def test_api_responses_are_not_cached_and_unknown_api_is_404(client: TestClient) -> None:
    headers, _ = make_board(client)
    assert client.get("/api/board", headers=headers).headers["cache-control"] == "no-store"
    assert client.get("/api/nothing").status_code == 404
    assert client.get("/health").headers.get("cache-control") != "no-store"


def test_family_limit_is_counted_without_the_code(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AI_FAMILY_DAILY_LIMIT", "2")
    consume_family_call(engine, "family-one-code")
    consume_family_call(engine, "family-one-code")
    with pytest.raises(DailyLimitReached):
        consume_family_call(engine, "family-one-code")
    consume_family_call(engine, "family-two-code")
    with engine.connect() as conn:
        rows = conn.execute(select(ai_call_counts)).all()
    counts = {row.scope: row.count for row in rows}
    assert counts[family_scope("family-one-code")] == 2
    assert counts["global"] == 3
    assert not any("family-one-code" in scope for scope in counts)


def test_global_limit_also_stops(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_DAILY_LIMIT", "1")
    consume_family_call(engine, "a")
    with pytest.raises(DailyLimitReached):
        consume_family_call(engine, "b")
