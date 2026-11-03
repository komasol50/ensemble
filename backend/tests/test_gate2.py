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


# ---- 消す：案の根拠でも消せる ----


def deleted(client: TestClient, headers: dict, board: dict, item_id: str) -> dict:
    res = client.delete(
        f"/api/board/items/{item_id}", headers=headers, params={"version": board["version"]}
    )
    assert res.status_code == 200, res.text
    return res.json()


def two_action_board(client: TestClient) -> tuple[dict, dict]:
    headers, board = new_board(client)
    saved = accept(
        client,
        headers,
        board,
        [
            dict(FAMILY_ACTION),
            {
                "part": "保育園・預け先",
                "type": "空きを確認する",
                "draft": "延長の空きを確認させてください",
                "basis_ids": ["05", "結果"],
                "release": ["a-05"],
            },
        ],
    )
    return headers, saved


def test_basis_item_can_be_removed_and_only_its_action_is_withdrawn(client: TestClient) -> None:
    headers, saved = two_action_board(client)
    branch_id = saved["branches"][0]["id"]
    after = deleted(client, headers, saved, "a-02")
    ids = {item["id"] for item in after["items"]}
    assert "a-02" not in ids
    # 家族への案だけ取り下げ、未送信の連絡の項目も消える。保育園の案は残る。
    assert f"s-{branch_id}-0-0" not in ids
    assert f"s-{branch_id}-0-1" in ids
    actions = after["branches"][0]["outcomes"][0]["actions"]
    assert actions[0]["type"] == "何もしない" and actions[0]["draft"] is None
    assert actions[1]["draft"] == "延長の空きを確認させてください"


def test_sent_message_stays_when_its_basis_is_removed(client: TestClient) -> None:
    """送った連絡は事実なので、根拠を消しても項目を残す。"""
    headers, saved = two_action_board(client)
    sent = f"s-{saved['branches'][0]['id']}-0-0"
    asked = client.patch(
        f"/api/board/items/{sent}",
        json={"version": saved["version"], "status": "頼んだ"},
        headers=headers,
    ).json()
    after = deleted(client, headers, asked, "a-02")
    assert item_of(after, sent)["status_label"] == "返答待ち"


def test_removing_the_condition_withdraws_the_whole_branch(client: TestClient) -> None:
    headers, saved = two_action_board(client)
    after = deleted(client, headers, saved, "a-07")
    assert after["branches"] == []
    assert not [item for item in after["items"] if item["id"].startswith("s-")]


def test_removing_the_result_withdraws_all_that_cite_it(client: TestClient) -> None:
    headers, saved = two_action_board(client)
    result = next(i for i in saved["items"] if i["text_original"] == RESULT_ITEM["text_original"])
    after = deleted(client, headers, saved, result["id"])
    assert after["branches"] == []


def test_released_item_can_be_removed_without_withdrawing(client: TestClient) -> None:
    headers, board = new_board(client)
    saved = accept(
        client,
        headers,
        board,
        [
            {
                "part": "保育園・預け先",
                "type": "空きを確認する",
                "draft": "延長の空きを確認させてください",
                "basis_ids": ["07", "結果"],
                "release": ["a-05"],
            }
        ],
    )
    after = deleted(client, headers, saved, "a-05")
    action = after["branches"][0]["outcomes"][0]["actions"][0]
    assert action["release"] == [] and action["draft"]


def test_removing_a_referred_item_clears_the_reference(client: TestClient) -> None:
    headers, board = new_board(client)
    added = client.post(
        "/api/board/items",
        json={"version": board["version"], **RESULT_ITEM, "refers_to": "a-07"},
        headers=headers,
    ).json()
    after = deleted(client, headers, added, "a-07")
    assert added["items"][-1]["id"] in {item["id"] for item in after["items"]}
    assert after["items"][-1]["refers_to"] is None


def test_new_board_is_recorded_today(client: TestClient) -> None:
    """プリセットの記録の日のままだと、週の表の曜日がずれる。"""
    _, board = new_board(client)
    today = ensemble.api.now_jst()[:10]
    assert {item["recorded_at"][:10] for item in board["items"]} == {today}
    assert board["week"] is not None


# ---- ゴミ箱から戻す ----


def trash_of(client: TestClient, headers: dict) -> list[dict]:
    return client.get("/api/board/trash", headers=headers).json()["entries"]


def test_removed_item_goes_to_the_trash_and_can_come_back(client: TestClient) -> None:
    headers, board = new_board(client)
    after = deleted(client, headers, board, "a-03")
    assert "a-03" not in {item["id"] for item in after["items"]}
    entries = trash_of(client, headers)
    assert [entry["item_id"] for entry in entries] == ["a-03"]
    assert entries[0]["text"] == "洗濯物の持ち帰り" and entries[0]["withdrew"] is False
    restored = client.post(f"/api/board/trash/{entries[0]['id']}/restore", headers=headers).json()
    assert [item["id"] for item in restored["items"]] == [item["id"] for item in board["items"]]
    assert item_of(restored, "a-03")["status_label"] == "引き受けた"
    assert trash_of(client, headers) == []


def test_restore_brings_back_the_withdrawn_action_and_message(client: TestClient) -> None:
    headers, saved = two_action_board(client)
    branch_id = saved["branches"][0]["id"]
    after = deleted(client, headers, saved, "a-02")
    assert after["branches"][0]["outcomes"][0]["actions"][0]["type"] == "何もしない"
    entry = trash_of(client, headers)[0]
    assert entry["withdrew"] is True
    back = client.post(f"/api/board/trash/{entry['id']}/restore", headers=headers).json()
    action = back["branches"][0]["outcomes"][0]["actions"][0]
    assert action["draft"] == "着替え3日分を持ってきてください"
    assert f"s-{branch_id}-0-0" in {item["id"] for item in back["items"]}


def test_restore_brings_back_a_withdrawn_branch(client: TestClient) -> None:
    headers, saved = two_action_board(client)
    after = deleted(client, headers, saved, "a-07")
    assert after["branches"] == []
    entry = trash_of(client, headers)[0]
    back = client.post(f"/api/board/trash/{entry['id']}/restore", headers=headers).json()
    assert back["branches"] == saved["branches"]
    assert {item["id"] for item in back["items"]} == {item["id"] for item in saved["items"]}


def test_restore_brings_back_a_release_and_a_reference(client: TestClient) -> None:
    headers, board = new_board(client)
    saved = accept(
        client,
        headers,
        board,
        [
            {
                "part": "保育園・預け先",
                "type": "空きを確認する",
                "draft": "延長の空きを確認させてください",
                "basis_ids": ["07", "結果"],
                "release": ["a-05"],
            }
        ],
    )
    deleted(client, headers, saved, "a-05")
    entry = trash_of(client, headers)[0]
    back = client.post(f"/api/board/trash/{entry['id']}/restore", headers=headers).json()
    assert back["branches"][0]["outcomes"][0]["actions"][0]["release"] == ["a-05"]

    added = client.post(
        "/api/board/items",
        json={"version": back["version"], **RESULT_ITEM, "refers_to": "a-01"},
        headers=headers,
    ).json()
    cleared = deleted(client, headers, added, "a-01")
    assert cleared["items"][-1]["refers_to"] is None
    entry = trash_of(client, headers)[0]
    returned = client.post(f"/api/board/trash/{entry['id']}/restore", headers=headers).json()
    assert returned["items"][-1]["refers_to"] == "a-01"


def test_restore_does_not_overwrite_later_changes(client: TestClient) -> None:
    headers, saved = two_action_board(client)
    sent = f"s-{saved['branches'][0]['id']}-0-1"
    after = deleted(client, headers, saved, "a-02")
    asked = client.patch(
        f"/api/board/items/{sent}",
        json={"version": after["version"], "status": "頼んだ"},
        headers=headers,
    ).json()
    entry = trash_of(client, headers)[0]
    back = client.post(f"/api/board/trash/{entry['id']}/restore", headers=headers).json()
    assert item_of(back, sent)["status_label"] == "返答待ち"
    assert back["version"] == asked["version"] + 1


def test_purge_removes_it_for_good(client: TestClient) -> None:
    headers, board = new_board(client)
    deleted(client, headers, board, "a-03")
    entry = trash_of(client, headers)[0]
    assert client.delete(f"/api/board/trash/{entry['id']}", headers=headers).status_code == 200
    assert trash_of(client, headers) == []
    assert (
        client.post(f"/api/board/trash/{entry['id']}/restore", headers=headers).status_code == 404
    )


def test_trash_belongs_to_one_board(client: TestClient) -> None:
    headers, board = new_board(client)
    other, _ = new_board(client)
    deleted(client, headers, board, "a-03")
    entry = trash_of(client, headers)[0]
    assert trash_of(client, other) == []
    assert client.post(f"/api/board/trash/{entry['id']}/restore", headers=other).status_code == 404
    assert client.delete(f"/api/board/trash/{entry['id']}", headers=other).status_code == 404


def test_deleting_the_board_empties_the_trash(client: TestClient, engine: Engine) -> None:
    """同意の3行の約束。ゴミ箱に中身を残さない。"""
    from ensemble.store import list_trash

    headers, board = new_board(client)
    deleted(client, headers, board, "a-03")
    code = headers["X-Family-Code"]
    assert len(list_trash(engine, code)) == 1
    client.delete("/api/board", headers=headers)
    assert list_trash(engine, code) == []


def test_conflict_leaves_nothing_in_the_trash(client: TestClient) -> None:
    headers, board = new_board(client)
    res = client.delete(
        "/api/board/items/a-03", headers=headers, params={"version": board["version"] + 5}
    )
    assert res.status_code == 409
    assert trash_of(client, headers) == []


def test_trash_needs_a_real_board(client: TestClient) -> None:
    assert client.get("/api/board/trash").status_code == 404
