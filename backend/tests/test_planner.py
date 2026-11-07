"""持ち物と知らせをサーバーに持つ（2026-11-08 PO の決定）。台帳の外の表で、AIへは渡さない。"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine

from ensemble.main import create_app
from ensemble.retell import build_user_message
from ensemble.store import load_family

SECRET_NAME = "夜だけのぬいぐるみ"


@pytest.fixture
def client(engine: Engine, tmp_path: Path) -> TestClient:
    return TestClient(create_app(engine, dist=tmp_path))


def make(client: TestClient) -> tuple[str, dict]:
    made = client.post("/api/boards", json={"home": "home_a"}).json()
    return made["code"], {"X-Family-Code": made["code"]}


def planner() -> dict:
    return {
        "tasks": {"remind": {"a-06": "2026-11-09T08:00"}, "category": {}, "closed": {}},
        "checklist": {"marks": {"1": "have"}, "added": [{"id": "x1", "name": SECRET_NAME}]},
    }


def test_planner_round_trip_without_moving_version(client: TestClient) -> None:
    code, headers = make(client)
    before = client.get("/api/board", headers=headers).json()
    assert before["planner"] == {}
    saved = client.put("/api/board/planner", json={"planner": planner()}, headers=headers)
    assert saved.status_code == 200
    after = client.get("/api/board", headers=headers).json()
    assert after["planner"] == planner()
    # 台帳の版は進めない（ほかの端末の操作と競わせない）。
    assert after["version"] == before["version"]


def test_ledger_saves_do_not_overwrite_planner(client: TestClient) -> None:
    code, headers = make(client)
    client.put("/api/board/planner", json={"planner": planner()}, headers=headers)
    version = client.get("/api/board", headers=headers).json()["version"]
    client.patch(
        "/api/board/items/a-06", json={"version": version, "status": "完了"}, headers=headers
    )
    assert client.get("/api/board", headers=headers).json()["planner"] == planner()


def test_planner_rejects_unknown_keys_and_large_bodies(client: TestClient) -> None:
    _, headers = make(client)
    bad = client.put("/api/board/planner", json={"planner": {"other": {}}}, headers=headers)
    assert bad.status_code == 422
    huge = {"checklist": {"added": [{"name": "あ" * 30000}]}}
    assert (
        client.put("/api/board/planner", json={"planner": huge}, headers=headers).status_code == 422
    )
    assert (
        client.put(
            "/api/board/planner", json={"planner": {"tasks": []}}, headers=headers
        ).status_code
        == 422
    )


def test_planner_needs_a_board(client: TestClient) -> None:
    missing = client.put(
        "/api/board/planner", json={"planner": planner()}, headers={"X-Family-Code": "x" * 43}
    )
    assert missing.status_code == 404


def test_planner_is_removed_with_the_board(client: TestClient, engine: Engine) -> None:
    code, headers = make(client)
    client.put("/api/board/planner", json={"planner": planner()}, headers=headers)
    assert client.delete("/api/board", headers=headers).status_code == 200
    from ensemble.store import load_planner

    assert load_planner(engine, code) == {}


def test_planner_never_reaches_the_ai_input(client: TestClient, engine: Engine) -> None:
    code, headers = make(client)
    client.put("/api/board/planner", json={"planner": planner()}, headers=headers)
    family = load_family(engine, code)
    message = build_user_message(family, "a-07", "condition")
    assert SECRET_NAME not in message
    assert "2026-11-09T08:00" not in message
    # 台帳の形（families.ledger）にも入らない。
    assert "planner" not in family.model_dump()
