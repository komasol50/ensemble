"""編集と時刻。合格の条件：守りを飛ばして直せない／送った文は直せない／時刻は消すときに一緒に消える。"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import Engine

from ensemble.db import item_events
from ensemble.guards import MEDICAL_FIXED_TEXT
from ensemble.main import create_app

TO_FAMILY = {
    "part": "家族",
    "type": "頼む",
    "draft": "着替え3日分を持ってきてください",
    "basis_ids": ["a-03"],
    "release": [],
}


@pytest.fixture
def client(engine: Engine, tmp_path: Path) -> TestClient:
    return TestClient(create_app(engine, dist=tmp_path))


def make_board(client: TestClient) -> tuple[dict, dict]:
    res = client.post("/api/boards", json={"home": "home_a"})
    assert res.status_code == 201
    body = res.json()
    return {"X-Family-Code": body["code"]}, body["board"]


def item_of(board: dict, item_id: str) -> dict:
    return next(i for i in board["items"] if i["id"] == item_id)


def edit(client: TestClient, headers: dict, board: dict, item_id: str, **fields: object) -> object:
    return client.patch(
        f"/api/board/items/{item_id}/content",
        headers=headers,
        json={"version": board["version"], **fields},
    )


def add_item(client: TestClient, headers: dict, board: dict, **fields: object) -> dict:
    res = client.post(
        "/api/board/items",
        headers=headers,
        json={"version": board["version"], "certainty": "確定", **fields},
    )
    assert res.status_code == 201
    return res.json()


def accept(client: TestClient, headers: dict, board: dict) -> tuple[dict, str]:
    res = client.post(
        "/api/board/branches",
        headers=headers,
        json={
            "version": board["version"],
            "branch": {
                "id": "x",
                "condition_item_id": "a-03",
                "candidate_days": [],
                "outcomes": [{"label": "そのとき", "actions": [TO_FAMILY]}],
            },
        },
    )
    assert res.status_code == 201
    saved = res.json()
    return saved, saved["items"][-1]["id"]


def set_status(client: TestClient, headers: dict, board: dict, item_id: str, status: str) -> dict:
    res = client.patch(
        f"/api/board/items/{item_id}",
        headers=headers,
        json={"version": board["version"], "status": status},
    )
    assert res.status_code == 200
    return res.json()


# ---- 時刻 ----


def test_every_item_starts_with_the_time_it_was_entered(client: TestClient) -> None:
    _, board = make_board(client)
    for item in board["items"]:
        assert item["events"][0]["label"] == "入れた"
        assert item["events"][0]["at"] == item["recorded_at"]


def test_sending_and_reply_are_logged_in_order(client: TestClient) -> None:
    headers, board = make_board(client)
    saved, item_id = accept(client, headers, board)
    assert [e["label"] for e in item_of(saved, item_id)["events"]] == ["文を作った"]

    sent = set_status(client, headers, saved, item_id, "頼んだ")
    assert [e["label"] for e in item_of(sent, item_id)["events"]] == ["文を作った", "送った"]

    res = client.post(
        f"/api/board/items/{item_id}/reply", headers=headers, json={"answer": "持っていく"}
    )
    assert res.status_code == 200
    replied = res.json()
    assert [e["label"] for e in item_of(replied, item_id)["events"]] == [
        "文を作った",
        "送った",
        "夫：引き受けた",
    ]

    done = set_status(client, headers, replied, item_id, "完了")
    assert item_of(done, item_id)["events"][-1]["label"] == "済"
    # 読み直しても同じ。
    again = client.get("/api/board", headers=headers).json()
    assert item_of(again, item_id)["events"] == item_of(done, item_id)["events"]


def test_pressing_the_same_status_again_does_not_log_twice(client: TestClient) -> None:
    headers, board = make_board(client)
    saved, item_id = accept(client, headers, board)
    sent = set_status(client, headers, saved, item_id, "頼んだ")
    again = set_status(client, headers, sent, item_id, "頼んだ")
    assert [e["label"] for e in item_of(again, item_id)["events"]] == ["文を作った", "送った"]


def test_the_same_family_answer_does_not_log_twice(client: TestClient) -> None:
    headers, board = make_board(client)
    saved, item_id = accept(client, headers, board)
    set_status(client, headers, saved, item_id, "頼んだ")
    for _ in range(2):
        res = client.post(
            f"/api/board/items/{item_id}/reply", headers=headers, json={"answer": "持っていく"}
        )
    labels = [e["label"] for e in item_of(res.json(), item_id)["events"]]
    assert labels.count("夫：引き受けた") == 1


def test_reply_with_time_keeps_the_time_in_the_log(client: TestClient) -> None:
    headers, board = make_board(client)
    saved, item_id = accept(client, headers, board)
    res = client.post(
        f"/api/board/items/{item_id}/reply",
        headers=headers,
        json={"answer": "◯時なら", "when": "今日の夜"},
    )
    assert item_of(res.json(), item_id)["events"][-1]["label"] == "夫：今日の夜なら"


def test_the_failed_version_does_not_log(client: TestClient, engine: Engine) -> None:
    headers, board = make_board(client)
    res = client.patch(
        "/api/board/items/a-02",
        headers=headers,
        json={"version": board["version"] + 5, "status": "頼んだ"},
    )
    assert res.status_code == 409
    with engine.connect() as conn:
        assert conn.execute(select(item_events)).all() == []


def test_asking_the_ward_is_logged_in_those_words(client: TestClient) -> None:
    headers, board = make_board(client)
    saved = add_item(
        client,
        headers,
        board,
        kind="質問",
        part="病院",
        status="未確認",
        medical="ask",
        text_original="薬を増やしたほうがいいですか",
        speaker="親",
    )
    item_id = saved["items"][-1]["id"]
    done = set_status(client, headers, saved, item_id, "答えあり")
    assert item_of(done, item_id)["events"][-1]["label"] == "病棟に聞いた"
    assert item_of(done, item_id)["medical_reply"] == MEDICAL_FIXED_TEXT


def test_undoing_done_is_logged_as_undone_not_as_a_new_reply(client: TestClient) -> None:
    headers, board = make_board(client)
    saved, item_id = accept(client, headers, board)
    sent = set_status(client, headers, saved, item_id, "頼んだ")
    replied = client.post(
        f"/api/board/items/{item_id}/reply", headers=headers, json={"answer": "持っていく"}
    ).json()
    done = set_status(client, headers, replied, item_id, "完了")
    back = set_status(client, headers, done, item_id, "引き受けた")
    assert sent["version"] < back["version"]
    assert [e["label"] for e in item_of(back, item_id)["events"]] == [
        "文を作った",
        "送った",
        "夫：引き受けた",
        "済",
        "元に戻した",
    ]


def test_events_hold_no_text(client: TestClient, engine: Engine) -> None:
    headers, board = make_board(client)
    saved, item_id = accept(client, headers, board)
    set_status(client, headers, saved, item_id, "頼んだ")
    with engine.connect() as conn:
        labels = [row.label for row in conn.execute(select(item_events)).all()]
    assert labels == ["送った"]


def test_deleting_the_board_deletes_the_times(client: TestClient, engine: Engine) -> None:
    headers, board = make_board(client)
    saved, item_id = accept(client, headers, board)
    set_status(client, headers, saved, item_id, "頼んだ")
    assert client.delete("/api/board", headers=headers).status_code == 200
    with engine.connect() as conn:
        assert conn.execute(select(item_events)).all() == []


def test_times_survive_the_trash_until_purged(client: TestClient, engine: Engine) -> None:
    headers, board = make_board(client)
    saved, item_id = accept(client, headers, board)
    sent = set_status(client, headers, saved, item_id, "頼んだ")
    removed = client.delete(
        f"/api/board/items/{item_id}?version={sent['version']}", headers=headers
    )
    assert removed.status_code == 200
    trash_id = client.get("/api/board/trash", headers=headers).json()["entries"][0]["id"]

    restored = client.post(f"/api/board/trash/{trash_id}/restore", headers=headers).json()
    assert item_of(restored, item_id)["events"][-1]["label"] == "送った"

    removed = client.delete(
        f"/api/board/items/{item_id}?version={restored['version']}", headers=headers
    )
    trash_id = client.get("/api/board/trash", headers=headers).json()["entries"][0]["id"]
    assert client.delete(f"/api/board/trash/{trash_id}", headers=headers).status_code == 200
    with engine.connect() as conn:
        assert conn.execute(select(item_events)).all() == []


def test_purging_one_keeps_the_times_of_another_in_the_trash(
    client: TestClient, engine: Engine
) -> None:
    headers, board = make_board(client)
    saved, sent_id = accept(client, headers, board)
    saved = set_status(client, headers, saved, sent_id, "頼んだ")
    other = set_status(client, headers, saved, "a-02", "頼んだ")
    first = client.delete(f"/api/board/items/{sent_id}?version={other['version']}", headers=headers)
    client.delete(f"/api/board/items/a-02?version={first.json()['version']}", headers=headers)
    entries = client.get("/api/board/trash", headers=headers).json()["entries"]
    target = next(entry for entry in entries if entry["item_id"] == "a-02")
    client.delete(f"/api/board/trash/{target['id']}", headers=headers)
    with engine.connect() as conn:
        rows = conn.execute(select(item_events)).all()
    assert {row.item_id for row in rows} == {sent_id}


# ---- 編集 ----


def test_edit_changes_the_text_and_logs_it(client: TestClient) -> None:
    headers, board = make_board(client)
    saved = add_item(
        client,
        headers,
        board,
        kind="情報",
        part="親",
        certainty="確定",
        text_original="昨日はパンだけ",
        speaker="親",
    )
    item_id = saved["items"][-1]["id"]
    res = edit(client, headers, saved, item_id, text_original="昨日はパンとスープ")
    assert res.status_code == 200
    changed = item_of(res.json(), item_id)
    assert changed["text_original"] == "昨日はパンとスープ"
    assert [e["label"] for e in changed["events"]] == ["入れた", "直した"]
    assert res.json()["version"] == saved["version"] + 1


def test_edit_with_no_change_keeps_the_version_and_log(client: TestClient) -> None:
    headers, board = make_board(client)
    item = item_of(board, "a-02")
    res = edit(client, headers, board, "a-02", owner=item["owner"])
    assert res.status_code == 200
    assert res.json()["version"] == board["version"]
    assert [e["label"] for e in item_of(res.json(), "a-02")["events"]] == ["入れた"]


def test_edit_needs_the_current_version(client: TestClient) -> None:
    headers, board = make_board(client)
    res = client.patch(
        "/api/board/items/a-02/content",
        headers=headers,
        json={"version": board["version"] + 1, "owner": "夫"},
    )
    assert res.status_code == 409


def test_edit_rejects_unknown_fields_and_unknown_items(client: TestClient) -> None:
    headers, board = make_board(client)
    assert edit(client, headers, board, "a-02", status="完了").status_code == 422
    assert edit(client, headers, board, "a-02", medical="なし").status_code == 422
    assert edit(client, headers, board, "a-02", certainty="確定").status_code == 422
    assert edit(client, headers, board, "nope", owner="夫").status_code == 404


def test_edit_owner_and_due_can_be_cleared(client: TestClient) -> None:
    headers, board = make_board(client)
    res = edit(client, headers, board, "a-02", owner="母", due="木曜の朝")
    item = item_of(res.json(), "a-02")
    assert (item["owner"], item["due"]) == ("母", "木曜の朝")
    res = edit(client, headers, res.json(), "a-02", owner=" ", due=None)
    item = item_of(res.json(), "a-02")
    assert (item["owner"], item["due"]) == (None, None)


def test_edit_cannot_empty_the_text(client: TestClient) -> None:
    headers, board = make_board(client)
    res = edit(client, headers, board, "a-02", task_summary="   ")
    assert res.status_code == 422


def test_edit_limits_the_length(client: TestClient) -> None:
    headers, board = make_board(client)
    assert edit(client, headers, board, "a-02", task_summary="あ" * 2001).status_code == 422
    assert edit(client, headers, board, "a-02", due="あ" * 51).status_code == 422
    assert edit(client, headers, board, "a-02", owner="あ" * 21).status_code == 422


def test_unsent_message_can_be_edited_and_the_plan_follows(client: TestClient) -> None:
    headers, board = make_board(client)
    saved, item_id = accept(client, headers, board)
    res = edit(client, headers, saved, item_id, text_original="着替え2日分をお願いします")
    assert res.status_code == 200
    body = res.json()
    assert item_of(body, item_id)["text_original"] == "着替え2日分をお願いします"
    assert body["branches"][0]["outcomes"][0]["actions"][0]["draft"] == "着替え2日分をお願いします"


def test_sent_message_cannot_be_rewritten(client: TestClient) -> None:
    headers, board = make_board(client)
    saved, item_id = accept(client, headers, board)
    sent = set_status(client, headers, saved, item_id, "頼んだ")
    res = edit(client, headers, sent, item_id, text_original="別の文")
    assert res.status_code == 422
    assert "送った後の文" in res.json()["detail"]
    # 担当と期日は直せる。
    assert edit(client, headers, sent, item_id, due="明日の朝").status_code == 200


def test_sent_family_request_by_the_parent_cannot_be_rewritten(client: TestClient) -> None:
    headers, board = make_board(client)
    saved = add_item(
        client,
        headers,
        board,
        kind="行動",
        part="家族",
        status="頼んだ",
        text_original="充電器を持ってきて",
        speaker="親",
    )
    item_id = saved["items"][-1]["id"]
    assert edit(client, headers, saved, item_id, text_original="別の文").status_code == 422


def test_medical_words_cannot_be_added_to_a_message(client: TestClient) -> None:
    headers, board = make_board(client)
    saved, item_id = accept(client, headers, board)
    res = edit(client, headers, saved, item_id, text_original="薬の量を増やすと伝えて")
    assert res.status_code == 422
    assert "医療の語" in res.json()["detail"]
    # 守りで落ちたら、案の文も変わらない。
    again = client.get("/api/board", headers=headers).json()
    assert again["branches"][0]["outcomes"][0]["actions"][0]["draft"] == TO_FAMILY["draft"]
    assert again["version"] == saved["version"]


def test_message_cannot_move_to_another_part(client: TestClient) -> None:
    headers, board = make_board(client)
    saved, item_id = accept(client, headers, board)
    assert edit(client, headers, saved, item_id, part="職場").status_code == 422


def test_third_party_item_gets_no_original(client: TestClient) -> None:
    headers, board = make_board(client)
    saved = add_item(
        client,
        headers,
        board,
        kind="行動",
        part="職場",
        status="頼みたい",
        text_original="資料をお願いします",
        task_summary="資料を送る",
        speaker="上司",
    )
    item_id = saved["items"][-1]["id"]
    assert item_of(saved, item_id)["text_original"] is None
    assert edit(client, headers, saved, item_id, text_original="別の文").status_code == 422
    res = edit(client, headers, saved, item_id, task_summary="資料を明日送る")
    assert res.status_code == 200
    changed = item_of(res.json(), item_id)
    assert changed["task_summary"] == "資料を明日送る"
    assert changed["text_original"] is None


def test_original_cannot_move_to_a_third_party(client: TestClient) -> None:
    headers, board = make_board(client)
    saved = add_item(
        client,
        headers,
        board,
        kind="情報",
        part="病院",
        text_original="木曜に退院できるかも",
        speaker="医師",
    )
    item_id = saved["items"][-1]["id"]
    assert edit(client, headers, saved, item_id, part="職場").status_code == 422
    assert edit(client, headers, saved, item_id, part="家族").status_code == 200


def test_own_note_stays_with_the_parent(client: TestClient) -> None:
    """自分のことはAIに送らない約束なので、ほかの相手へ移さない。"""
    headers, board = make_board(client)
    saved = add_item(
        client, headers, board, kind="情報", part="親", text_original="眠れた", speaker="親"
    )
    item_id = saved["items"][-1]["id"]
    assert edit(client, headers, saved, item_id, part="家族").status_code == 422
    assert edit(client, headers, saved, item_id, part="親").status_code == 422


def test_moving_a_request_to_the_family_names_who_answers(client: TestClient) -> None:
    headers, board = make_board(client)
    saved = add_item(
        client,
        headers,
        board,
        kind="行動",
        part="支援",
        status="頼みたい",
        text_original="買い物を頼む",
        speaker="親",
    )
    item_id = saved["items"][-1]["id"]
    res = edit(client, headers, saved, item_id, part="家族")
    assert res.status_code == 200
    assert item_of(res.json(), item_id)["owner"] == "夫"


def test_edited_text_gets_the_same_guards(client: TestClient) -> None:
    """症状を書き足したら固定文に。条件の言葉を書いたら確かさを見込みに下げる。"""
    headers, board = make_board(client)
    saved = add_item(
        client,
        headers,
        board,
        kind="情報",
        part="病院",
        text_original="11月5日に検査がある",
        speaker="親",
    )
    item_id = saved["items"][-1]["id"]
    assert item_of(saved, item_id)["certainty"] == "確定"
    res = edit(client, headers, saved, item_id, text_original="11月5日に検査があるかも")
    assert item_of(res.json(), item_id)["certainty"] == "見込み"
    res = edit(client, headers, res.json(), item_id, text_original="ぐったりしている")
    assert item_of(res.json(), item_id)["medical_reply"] == MEDICAL_FIXED_TEXT


def test_medical_ask_stays_fixed_after_editing(client: TestClient) -> None:
    headers, board = make_board(client)
    saved = add_item(
        client,
        headers,
        board,
        kind="質問",
        part="病院",
        status="未確認",
        medical="ask",
        text_original="薬を増やしたほうがいいですか",
        speaker="親",
    )
    item_id = saved["items"][-1]["id"]
    res = edit(client, headers, saved, item_id, text_original="売店は何時まで")
    assert item_of(res.json(), item_id)["medical_reply"] == MEDICAL_FIXED_TEXT


def test_certainty_is_not_touched_when_the_text_stays(client: TestClient) -> None:
    headers, board = make_board(client)
    item = item_of(board, "a-05")
    res = edit(client, headers, board, "a-05", owner="夫")
    assert item_of(res.json(), "a-05")["certainty"] == item["certainty"]


def test_due_of_a_time_reply_is_not_edited_here(client: TestClient) -> None:
    headers, board = make_board(client)
    saved, item_id = accept(client, headers, board)
    replied = client.post(
        f"/api/board/items/{item_id}/reply",
        headers=headers,
        json={"answer": "◯時なら", "when": "今日の夜"},
    ).json()
    assert edit(client, headers, replied, item_id, due="明日").status_code == 422


def test_aliases_are_masked_when_saved_and_shown_back(client: TestClient, engine: Engine) -> None:
    from ensemble.store import load_family, save_family

    headers, board = make_board(client)
    code = headers["X-Family-Code"]
    family = load_family(engine, code)
    family.aliases = {"たろう": "［子］"}
    save_family(engine, family, family.version)
    current = client.get("/api/board", headers=headers).json()
    res = edit(client, headers, current, "a-02", task_summary="たろうの着替え")
    assert item_of(res.json(), "a-02")["task_summary"] == "たろうの着替え"
    stored = next(i for i in load_family(engine, code).items if i.id == "a-02")
    assert stored.task_summary == "［子］の着替え"
