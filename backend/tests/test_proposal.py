"""1つの問いのAPI。合格の条件：押す前は台帳が変わらない／戻ってきた案にも守りが当たる。"""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine

from ensemble.guards import CANNOT_PROPOSE_TEXT
from ensemble.ledger import ActionType, Branch, Part
from ensemble.main import create_app
from ensemble.retell import MissingKey
from ensemble.store import load_family, save_family


def branch(draft: str = "木曜は取り消し、火曜の延長の空きを確認させてください") -> Branch:
    return Branch.model_validate(
        {
            "id": "branch-1",
            "condition_item_id": "a-07",
            "candidate_days": [
                {"day": "木曜", "certainty": "見込み", "cancelled": True},
                {"day": "火曜", "certainty": "見込み"},
            ],
            "outcomes": [
                {
                    "label": "採血がだめだった",
                    "actions": [
                        {
                            "part": Part.CHILDCARE,
                            "type": ActionType.CHECK_AVAILABILITY,
                            "draft": draft,
                            "basis_ids": ["a-05", "a-07"],
                            "release": ["a-05"],
                        }
                    ],
                }
            ],
        }
    )


class FakeClient:
    def __init__(self) -> None:
        self.outputs: list[Branch | None] = []
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


@pytest.fixture
def fake() -> FakeClient:
    return FakeClient()


@pytest.fixture
def client(engine: Engine, tmp_path: Path, fake: FakeClient) -> TestClient:
    return TestClient(create_app(engine, dist=tmp_path, client_factory=lambda: fake))


RESULT_ITEM = {
    "kind": "情報",
    "part": "病院",
    "certainty": "確定",
    "text_original": "今日の採血はだめでした",
    "speaker": "親",
    "medical": "info",
    "refers_to": "a-07",
}


def board_with_result(client: TestClient) -> tuple[dict, dict, str]:
    """家庭Aのボードに、親が入れた結果の項目を足す。"""
    made = client.post("/api/boards", json={"home": "home_a"}).json()
    headers = {"X-Family-Code": made["code"]}
    board = client.post(
        "/api/board/items", json={"version": 1, **RESULT_ITEM}, headers=headers
    ).json()
    return headers, board, board["items"][-1]["id"]


def propose(client: TestClient, headers: dict, item_id: str) -> Any:
    return client.post("/api/board/proposal", json={"item_id": item_id}, headers=headers)


def test_proposal_does_not_change_the_ledger(client: TestClient, fake: FakeClient) -> None:
    headers, board, item_id = board_with_result(client)
    fake.outputs.append(branch())
    body = propose(client, headers, item_id).json()
    assert body["stage"] == "result"
    assert body["branch"]["candidate_days"][0]["cancelled"] is True
    after = client.get("/api/board", headers=headers).json()
    assert after == board


def test_accept_stores_the_branch_and_keeps_every_status(
    client: TestClient, fake: FakeClient
) -> None:
    """外す項目も消さず、状態も変えない。送ったかどうかは親が決める。"""
    headers, board, item_id = board_with_result(client)
    fake.outputs.append(branch())
    proposal = propose(client, headers, item_id).json()
    res = client.post(
        "/api/board/branches",
        json={"version": board["version"], "branch": proposal["branch"]},
        headers=headers,
    )
    assert res.status_code == 201
    saved = res.json()
    assert saved["version"] == board["version"] + 1
    assert len(saved["branches"]) == 1
    assert saved["branches"][0]["id"] != "branch-1"

    # 今ある項目は1つも変わらず、連絡の分だけ未依頼の項目が増える。
    # 根拠になると原文を直せなくなるので、その表示の欄だけ比べない。
    def stored(items: list[dict]) -> list[dict]:
        return [{k: v for k, v in item.items() if k != "original_fixed"} for item in items]

    assert stored(saved["items"][: len(board["items"])]) == stored(board["items"])
    added = saved["items"][len(board["items"]) :]
    assert [item["status_label"] for item in added] == ["未依頼"]


def test_accept_with_an_old_version_fails(client: TestClient, fake: FakeClient) -> None:
    headers, board, item_id = board_with_result(client)
    fake.outputs.append(branch())
    proposal = propose(client, headers, item_id).json()
    client.patch(
        "/api/board/items/a-02",
        json={"version": board["version"], "status": "引き受けた"},
        headers=headers,
    )
    res = client.post(
        "/api/board/branches",
        json={"version": board["version"], "branch": proposal["branch"]},
        headers=headers,
    )
    assert res.status_code == 409
    assert client.get("/api/board", headers=headers).json()["branches"] == []


def tampered(mutate: Any) -> dict:
    data = branch().model_dump(mode="json")
    mutate(data)
    return data


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d["outcomes"][0]["actions"][0].update(basis_ids=["a-99"]),
        lambda d: d["outcomes"][0]["actions"][0].update(release=["a-99"]),
        lambda d: d["outcomes"][0]["actions"][0].update(
            draft="薬を増やしてください", basis_ids=["a-05"]
        ),
        lambda d: d.update(condition_item_id="a-99"),
        lambda d: d["candidate_days"].append(
            {"day": "金曜", "certainty": "確定", "cancelled": False}
        ),
    ],
)
def test_guards_run_again_on_the_way_back(client: TestClient, mutate: Any) -> None:
    """案は端末から戻ってくるので、作ったときの守りを信じない。"""
    headers, board, _ = board_with_result(client)
    res = client.post(
        "/api/board/branches",
        json={"version": board["version"], "branch": tampered(mutate)},
        headers=headers,
    )
    assert res.status_code == 422
    assert client.get("/api/board", headers=headers).json()["branches"] == []


def test_medical_ask_gets_no_proposal(client: TestClient, fake: FakeClient) -> None:
    made = client.post("/api/boards", json={"home": "home_a"}).json()
    headers = {"X-Family-Code": made["code"]}
    board = client.post(
        "/api/board/items",
        json={
            "version": 1,
            "kind": "質問",
            "part": "病院",
            "certainty": "見込み",
            "text_original": "採血がだめなら薬を増やしますか",
            "medical": "ask",
            "refers_to": "a-07",
            "status": "未確認",
        },
        headers=headers,
    ).json()
    res = propose(client, headers, board["items"][-1]["id"])
    assert res.status_code == 422 and fake.calls == []


def test_ordinary_item_gets_no_proposal(client: TestClient, fake: FakeClient) -> None:
    made = client.post("/api/boards", json={"home": "home_a"}).json()
    res = propose(client, {"X-Family-Code": made["code"]}, "a-02")
    assert res.status_code == 422 and fake.calls == []


def test_unknown_item_is_404(client: TestClient) -> None:
    made = client.post("/api/boards", json={"home": "home_a"}).json()
    assert propose(client, {"X-Family-Code": made["code"]}, "nope").status_code == 404


def test_guard_failure_twice_returns_the_message(client: TestClient, fake: FakeClient) -> None:
    headers, _, item_id = board_with_result(client)
    bad = branch("金曜の延長の空きを確認します")
    fake.outputs += [bad, bad]
    body = propose(client, headers, item_id).json()
    assert body["branch"] is None and body["message"] == CANNOT_PROPOSE_TEXT
    assert len(fake.calls) == 2


def test_limit_stops_the_proposal(
    client: TestClient, fake: FakeClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers, _, item_id = board_with_result(client)
    monkeypatch.setenv("AI_FAMILY_DAILY_LIMIT", "0")
    assert propose(client, headers, item_id).status_code == 429
    assert fake.calls == []


def test_missing_key_is_a_quiet_failure(engine: Engine, tmp_path: Path) -> None:
    def no_key() -> Any:
        raise MissingKey("x")

    client = TestClient(create_app(engine, dist=tmp_path, client_factory=no_key))
    headers, _, item_id = board_with_result(client)
    res = propose(client, headers, item_id)
    assert res.status_code == 503 and res.json()["detail"] == CANNOT_PROPOSE_TEXT


def test_aliases_are_hidden_in_storage_and_shown_on_screen(
    client: TestClient, fake: FakeClient, engine: Engine
) -> None:
    headers, board, item_id = board_with_result(client)
    code = headers["X-Family-Code"]
    family = load_family(engine, code)
    family.aliases = {"たろう": "［子］"}
    saved = save_family(engine, family, family.version)
    fake.outputs.append(branch("［子］の木曜は取り消し、火曜の延長の空きを確認させてください"))
    proposal = propose(client, headers, item_id).json()
    assert "たろう" in proposal["branch"]["outcomes"][0]["actions"][0]["draft"]
    shown = client.post(
        "/api/board/branches",
        json={"version": saved.version, "branch": proposal["branch"]},
        headers=headers,
    ).json()
    assert "たろう" in shown["branches"][0]["outcomes"][0]["actions"][0]["draft"]
    stored = load_family(engine, code).branches[0].outcomes[0].actions[0].draft
    assert stored is not None and "たろう" not in stored and "［子］" in stored


def test_sending_again_does_not_call_the_ai_twice(client: TestClient, fake: FakeClient) -> None:
    """スマホの送り直しで費用を増やさず、同じ案を返す。"""
    headers, _, item_id = board_with_result(client)
    fake.outputs.append(branch())
    first = propose(client, headers, item_id).json()
    again = propose(client, headers, item_id).json()
    assert again == first
    assert len(fake.calls) == 1


def test_changed_ledger_makes_a_new_proposal(client: TestClient, fake: FakeClient) -> None:
    headers, board, item_id = board_with_result(client)
    fake.outputs.extend([branch(), branch()])
    propose(client, headers, item_id)
    client.patch(
        "/api/board/items/a-02",
        json={"version": board["version"], "status": "頼んだ"},
        headers=headers,
    )
    propose(client, headers, item_id)
    assert len(fake.calls) == 2


def test_proposal_shows_the_week_before_it_is_accepted(
    client: TestClient, fake: FakeClient
) -> None:
    headers, board, item_id = board_with_result(client)
    fake.outputs.append(branch())
    week = propose(client, headers, item_id).json()["week"]
    columns = [cell for row in week["rows"] for cell in row["cells"][0]]
    assert {"text": "取り消し", "style": "cancelled"} in columns
    assert {"text": "候補", "style": "likely"} in columns
    assert client.get("/api/board", headers=headers).json()["branches"] == []


@pytest.mark.parametrize(
    "draft", ["金曜の延長の空きを確認します", "復帰の見通しを連絡します", "親が書き直した文です"]
)
def test_parent_can_rewrite_a_draft(client: TestClient, draft: str) -> None:
    """親の文は親の言葉。日付と言葉の守りはAIの文のためのものなので当てない。"""
    headers, board, _ = board_with_result(client)
    res = client.post(
        "/api/board/branches",
        json={
            "version": board["version"],
            "branch": tampered(lambda d: d["outcomes"][0]["actions"][0].update(draft=draft)),
        },
        headers=headers,
    )
    assert res.status_code == 201
    assert any(item["text_original"] == draft for item in res.json()["items"])


def test_rewritten_draft_with_a_medical_word_cannot_be_sent(client: TestClient) -> None:
    headers, board, _ = board_with_result(client)
    res = client.post(
        "/api/board/branches",
        json={
            "version": board["version"],
            "branch": tampered(
                lambda d: d["outcomes"][0]["actions"][0].update(draft="薬を増やしてください")
            ),
        },
        headers=headers,
    )
    assert res.status_code == 422
    assert "医療の語" in res.json()["detail"]
    assert client.get("/api/board", headers=headers).json()["branches"] == []


def test_proposal_shows_the_impact_without_writing_it(client: TestClient, fake: FakeClient) -> None:
    """押す前の案にも線の中身が付く。台帳は変わらない（合格テスト#4）。"""
    headers, board, item_id = board_with_result(client)
    fake.outputs.append(branch())
    body = propose(client, headers, item_id).json()
    assert body["impact"]["nodes"]
    assert any(node["effects"] != ["そのまま"] for node in body["impact"]["nodes"])
    after = client.get("/api/board", headers=headers).json()
    assert after == board
    assert after["impact"] is None
