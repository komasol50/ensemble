"""関門2のAPI：連絡が項目になる／家族の4択が戻る／返事が来た／このボードを消す。"""

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine

import ensemble.api
from ensemble.ailog import forget_family, record_ai_call
from ensemble.ledger import Part, Status, status_label
from ensemble.main import create_app

RESULT_ITEM = {
    "kind": "情報",
    "part": "病院",
    "certainty": "確定",
    "text_original": "今日の採血はだめでした",
    "speaker": "親",
    "medical": "info",
}


@pytest.fixture
def client(engine: Engine, tmp_path: Path) -> TestClient:
    return TestClient(create_app(engine, dist=tmp_path))


def new_board(client: TestClient, home: str = "home_a") -> tuple[dict, dict]:
    made = client.post("/api/boards", json={"home": home}).json()
    return {"X-Family-Code": made["code"]}, made["board"]


def accept(client: TestClient, headers: dict, board: dict, actions: list[dict]) -> dict:
    """結果の項目を足し、案を受け入れる。AIは呼ばない。"""
    prefix = board["items"][0]["id"][0]
    board = client.post(
        "/api/board/items",
        json={"version": board["version"], **RESULT_ITEM},
        headers=headers,
    ).json()
    trigger = board["items"][-1]["id"]
    for action in actions:
        action["basis_ids"] = [
            trigger if basis == "結果" else f"{prefix}-{basis}" for basis in action["basis_ids"]
        ]
    res = client.post(
        "/api/board/branches",
        json={
            "version": board["version"],
            "branch": {
                "id": "x",
                "condition_item_id": f"{prefix}-07",
                "candidate_days": [],
                "outcomes": [{"label": "採血がだめだった", "actions": actions}],
            },
        },
        headers=headers,
    )
    assert res.status_code == 201, res.text
    return res.json()


def item_of(board: dict, item_id: str) -> dict:
    return next(item for item in board["items"] if item["id"] == item_id)


FAMILY_ACTION = {
    "part": "家族",
    "type": "頼む",
    "draft": "着替え3日分を持ってきてください",
    "basis_ids": ["02", "結果"],
}


def family_board(client: TestClient) -> tuple[dict, dict, str]:
    headers, board = new_board(client)
    saved = accept(client, headers, board, [dict(FAMILY_ACTION)])
    return headers, saved, saved["items"][-1]["id"]


# ---- 連絡が項目になる ----


def test_accept_makes_an_item_for_the_family_with_owner_and_location(client: TestClient) -> None:
    _, saved, item_id = family_board(client)
    branch_id = saved["branches"][0]["id"]
    assert item_id == f"s-{branch_id}-0-0"
    item = item_of(saved, item_id)
    assert item["status_label"] == "未依頼"
    assert item["text_original"] == "着替え3日分を持ってきてください"
    assert item["owner"] == "夫"
    assert item["location"] == "着替えは寝室のタンスの2段目"
    assert item["certainty"] == "確定"


def test_hospital_becomes_a_question_and_nothing_makes_no_item(client: TestClient) -> None:
    headers, board = new_board(client)
    before = len(board["items"])
    saved = accept(
        client,
        headers,
        board,
        [
            {
                "part": "病院",
                "type": "確認する",
                "draft": "退院の時刻を確認する",
                "basis_ids": ["01", "07"],
            },
            {"part": "職場", "type": "何もしない", "draft": None, "basis_ids": []},
        ],
    )
    added = saved["items"][before + 1 :]
    assert [(item["kind"], item["status"]) for item in added] == [("質問", "未確認")]


def test_item_from_only_likely_basis_is_not_confirmed(client: TestClient) -> None:
    """後の案の根拠になるので、確定へ上げない。"""
    headers, board = new_board(client)
    saved = accept(
        client,
        headers,
        board,
        [
            {
                "part": "支援",
                "type": "空きを確認する",
                "draft": "空きはありますか",
                "basis_ids": ["07"],
            }
        ],
    )
    assert saved["items"][-1]["certainty"] == "見込み"


# ---- 家族の4択 ----


@pytest.mark.parametrize(
    ("body", "status", "label"),
    [
        ({"answer": "持っていく"}, "引き受けた", "引き受けた"),
        ({"answer": "手配する"}, "引き受けた", "引き受けた"),
        ({"answer": "今日は難しい"}, "難しい", "難しい"),
        ({"answer": "◯時なら", "when": "明日の朝"}, "時刻つきで可", "明日の朝なら"),
    ],
)
def test_family_answers(client: TestClient, body: dict, status: str, label: str) -> None:
    headers, saved, item_id = family_board(client)
    res = client.post(f"/api/board/items/{item_id}/reply", json=body, headers=headers)
    assert res.status_code == 200, res.text
    item = item_of(res.json(), item_id)
    assert item["status"] == status
    assert item["status_label"] == label
    assert res.json()["version"] == saved["version"] + 1


def test_changing_the_answer_clears_the_time(client: TestClient) -> None:
    headers, _, item_id = family_board(client)
    url = f"/api/board/items/{item_id}/reply"
    client.post(url, json={"answer": "◯時なら", "when": "今日の夜"}, headers=headers)
    item = item_of(client.post(url, json={"answer": "持っていく"}, headers=headers).json(), item_id)
    assert item["due"] is None
    assert item["status_label"] == "引き受けた"


def test_same_answer_again_does_not_advance_the_version(client: TestClient) -> None:
    headers, _, item_id = family_board(client)
    url = f"/api/board/items/{item_id}/reply"
    first = client.post(url, json={"answer": "持っていく"}, headers=headers).json()
    again = client.post(url, json={"answer": "持っていく"}, headers=headers).json()
    assert again["version"] == first["version"]


@pytest.mark.parametrize(
    "body",
    [
        {"answer": "◯時なら"},
        {"answer": "◯時なら", "when": "来年"},
        {"answer": "知らない答え"},
        {"answer": "持っていく", "status": "完了"},
    ],
)
def test_wrong_answers_are_refused(client: TestClient, body: dict) -> None:
    headers, saved, item_id = family_board(client)
    res = client.post(f"/api/board/items/{item_id}/reply", json=body, headers=headers)
    assert res.status_code == 422
    assert client.get("/api/board", headers=headers).json() == saved


@pytest.mark.parametrize("item_id", ["a-05", "a-07", "a-04"])
def test_only_open_family_actions_take_an_answer(client: TestClient, item_id: str) -> None:
    """保育園の項目、情報の項目、完了の項目には答えられない。"""
    headers, _ = new_board(client)
    res = client.post(
        f"/api/board/items/{item_id}/reply", json={"answer": "持っていく"}, headers=headers
    )
    assert res.status_code == 422


def test_reply_needs_the_code_and_a_real_item(client: TestClient) -> None:
    headers, _, item_id = family_board(client)
    url = f"/api/board/items/{item_id}/reply"
    assert client.post(url, json={"answer": "持っていく"}).status_code == 404
    res = client.post(
        "/api/board/items/nothing/reply", json={"answer": "持っていく"}, headers=headers
    )
    assert res.status_code == 404


def test_reply_survives_a_parallel_update(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """親の画面が同時に版を進めても、答えは失われない。"""
    headers, saved, item_id = family_board(client)
    real_save = ensemble.api.save_family
    calls: list[int] = []

    def save_once_late(engine: Engine, family: Any, version: int) -> Any:
        if not calls:
            calls.append(1)
            client.patch(
                "/api/board/items/a-01",
                json={"version": version, "status": "答えあり"},
                headers=headers,
            )
        return real_save(engine, family, version)

    monkeypatch.setattr(ensemble.api, "save_family", save_once_late)
    res = client.post(
        f"/api/board/items/{item_id}/reply", json={"answer": "持っていく"}, headers=headers
    )
    assert res.status_code == 200
    board = res.json()
    assert item_of(board, item_id)["status"] == "引き受けた"
    assert item_of(board, "a-01")["status"] == "答えあり"


# ---- 返事が来た ----


def test_reply_with_time_leaves_the_other_support_waiting(client: TestClient) -> None:
    """家庭B：保育園が18:30まで可でも、ファミサポの案は返答待ちのまま残る。"""
    headers, board = new_board(client, "home_b")
    saved = accept(
        client,
        headers,
        board,
        [
            {
                "part": "保育園・預け先",
                "type": "空きを確認する",
                "draft": "延長の空きを確認させてください",
                "basis_ids": ["05", "結果"],
            },
            {
                "part": "支援",
                "type": "空きを確認する",
                "draft": "迎えの空きを確認させてください",
                "basis_ids": ["06", "結果"],
            },
        ],
    )
    childcare, support = saved["items"][-2]["id"], saved["items"][-1]["id"]
    version = saved["version"]
    for item_id in (childcare, support):
        version = client.patch(
            f"/api/board/items/{item_id}",
            json={"version": version, "status": "頼んだ"},
            headers=headers,
        ).json()["version"]
    after = client.patch(
        f"/api/board/items/{childcare}",
        json={"version": version, "status": "時刻つきで可", "due": "18:30"},
        headers=headers,
    ).json()
    assert item_of(after, childcare)["status_label"] == "18:30まで可"
    assert item_of(after, support)["status_label"] == "返答待ち"


def test_status_label_depends_on_who_answered() -> None:
    assert status_label(Status.OK_WITH_TIME, "明日の朝", Part.FAMILY) == "明日の朝なら"
    assert status_label(Status.OK_WITH_TIME, "18:30", Part.CHILDCARE) == "18:30まで可"
    assert status_label(Status.OK_WITH_TIME, None, Part.FAMILY) == "時刻つきで可"


# ---- このボードを消す ----


def test_delete_removes_only_that_board(client: TestClient) -> None:
    headers, _ = new_board(client)
    other, other_board = new_board(client)
    assert client.delete("/api/board", headers=headers).status_code == 200
    assert client.get("/api/board", headers=headers).status_code == 404
    assert client.get("/api/board", headers=other).json() == other_board


def test_delete_needs_a_real_code(client: TestClient) -> None:
    assert client.delete("/api/board").status_code == 404
    assert client.delete("/api/board", headers={"X-Family-Code": "x" * 43}).status_code == 404


def test_family_link_is_gone_after_delete(client: TestClient) -> None:
    headers, _, item_id = family_board(client)
    client.delete("/api/board", headers=headers)
    res = client.post(
        f"/api/board/items/{item_id}/reply", json={"answer": "持っていく"}, headers=headers
    )
    assert res.status_code == 404


def test_delete_also_clears_the_ai_log(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    headers, _ = new_board(client)
    called: list[str] = []
    monkeypatch.setattr(ensemble.api, "forget_family", called.append)
    client.delete("/api/board", headers=headers)
    assert called == [headers["X-Family-Code"]]


def test_forget_family_removes_only_that_family(tmp_path: Path) -> None:
    log = tmp_path / "ai.jsonl"
    record_ai_call("intake", {"text": "消す文"}, None, family_code="aaaaXXXX", path=log)
    record_ai_call("intake", {"text": "残す文"}, None, family_code="bbbbXXXX", path=log)
    forget_family("aaaaXXXX", path=log)
    lines = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert [line["input"]["text"] for line in lines] == ["残す文"]


def test_forget_family_without_a_log_is_quiet(tmp_path: Path) -> None:
    forget_family("aaaaXXXX", path=tmp_path / "none.jsonl")
