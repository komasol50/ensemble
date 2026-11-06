"""連絡する相手（① はじめに）。合格の条件：チェックを外した相手の下書きは、AIが書いても画面にも台帳にも出ない。"""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine

from ensemble.contacts import contacts_from_choices, drop_unchecked_contacts
from ensemble.ledger import ActionType, Branch, Part, Profile
from ensemble.main import create_app
from ensemble.presets import profile_from_choices
from ensemble.store import load_family, save_family


def branch() -> Branch:
    """夫・保育園・職場・病院への案を1つずつ持つ分かれ道（家庭Aの台帳で守りを通る形）。"""

    def action(part: Part, draft: str) -> dict[str, Any]:
        return {
            "part": part,
            "type": ActionType.CONFIRM,
            "draft": draft,
            "basis_ids": ["a-07"],
        }

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
                        action(Part.FAMILY, "迎えの件、また確認させてください"),
                        action(Part.CHILDCARE, "お迎えの件、また確認させてください"),
                        action(Part.WORK, "休みの件、また確認させてください"),
                        action(Part.HOSPITAL, "退院の時刻を確認する"),
                    ],
                },
            ],
        }
    )


def part_names(data: Branch | dict) -> list[str]:
    if isinstance(data, Branch):
        return [action.part.value for outcome in data.outcomes for action in outcome.actions]
    return [action["part"] for outcome in data["outcomes"] for action in outcome["actions"]]


# ---- 選んだ語から台帳の相手へ ----


def test_contacts_map_to_the_parts_on_screen() -> None:
    assert contacts_from_choices(["職場", "夫"]) == [Part.FAMILY, Part.WORK]
    assert contacts_from_choices([]) == []
    assert contacts_from_choices(None) is None


def test_profile_keeps_the_contacts_and_the_words_chosen() -> None:
    profile = profile_from_choices({"errand": "夫・パートナー", "contacts": ["保育園"]})
    assert profile.contacts == [Part.CHILDCARE]
    # 設定し直すときに画面へ戻すので、選んだ語のまま残す。
    assert profile.choices == {"errand": "夫・パートナー", "contacts": ["保育園"]}


def test_unset_contacts_drop_nobody() -> None:
    assert profile_from_choices({"errand": "いない"}).contacts is None
    assert profile_from_choices(None).contacts is None


# ---- 落とす関数 ----


def test_no_contacts_field_keeps_every_action() -> None:
    """試す家庭とあとで設定した家庭は、欄が無いので何も落とさない。"""
    original = branch()
    assert drop_unchecked_contacts(original, Profile(name="家庭A")) is original


def test_unchecked_contacts_are_dropped_and_the_others_stay() -> None:
    original = branch()
    kept = drop_unchecked_contacts(original, Profile(name="x", contacts=[Part.CHILDCARE]))
    # 外したのは夫と職場。病院への案は連絡する相手の外なので残す。
    assert part_names(kept) == ["保育園・預け先", "病院"]
    # 元の分かれ道は変えない。
    assert part_names(original) == ["家族", "保育園・預け先", "職場", "病院"]
    assert [outcome.label for outcome in kept.outcomes] == ["採血がだめだった"]


def test_all_checked_keeps_every_action() -> None:
    profile = Profile(name="x", contacts=[Part.FAMILY, Part.CHILDCARE, Part.WORK])
    assert part_names(drop_unchecked_contacts(branch(), profile)) == [
        "家族",
        "保育園・預け先",
        "職場",
        "病院",
    ]


def test_nothing_checked_leaves_only_the_hospital() -> None:
    kept = drop_unchecked_contacts(branch(), Profile(name="x", contacts=[]))
    assert part_names(kept) == ["病院"]


# ---- API：案を返す前と、保存する前 ----


class FakeClient:
    def __init__(self) -> None:
        self.outputs: list[Branch | None] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(parse=self.parse))

    def parse(self, **kwargs: Any) -> SimpleNamespace:
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


def board_with_contacts(
    client: TestClient, engine: Engine, contacts: list[Part] | None
) -> tuple[dict, int, str]:
    """家庭Aのボードの連絡する相手を変え、結果の項目を足す。"""
    made = client.post("/api/boards", json={"home": "home_a"}).json()
    headers = {"X-Family-Code": made["code"]}
    family = load_family(engine, made["code"])
    family.profile.contacts = contacts
    save_family(engine, family, family.version)
    version = client.get("/api/board", headers=headers).json()["version"]
    board = client.post(
        "/api/board/items", json={"version": version, **RESULT_ITEM}, headers=headers
    ).json()
    return headers, board["version"], board["items"][-1]["id"]


def test_proposal_has_no_draft_for_an_unchecked_contact(
    client: TestClient, engine: Engine, fake: FakeClient
) -> None:
    headers, _, item_id = board_with_contacts(client, engine, [Part.FAMILY, Part.CHILDCARE])
    fake.outputs.append(branch())
    body = client.post("/api/board/proposal", json={"item_id": item_id}, headers=headers).json()
    assert part_names(body["branch"]) == ["家族", "保育園・預け先", "病院"]
    # 線の図にも、外した相手への変更は出さない（台帳にもとからある職場の項目は別）。
    changed = [node["part"] for node in body["impact"]["nodes"] if node["change"]]
    assert "職場" not in changed


def test_board_without_contacts_gets_every_draft(
    client: TestClient, engine: Engine, fake: FakeClient
) -> None:
    headers, _, item_id = board_with_contacts(client, engine, None)
    fake.outputs.append(branch())
    body = client.post("/api/board/proposal", json={"item_id": item_id}, headers=headers).json()
    assert part_names(body["branch"]) == ["家族", "保育園・預け先", "職場", "病院"]
    # 落とさなければ、線の図に職場の変更が出る（上の検査が空振りしていないことの確かめ）。
    assert "職場" in [node["part"] for node in body["impact"]["nodes"] if node["change"]]


def test_accept_drops_an_unchecked_contact_sent_back_from_the_device(
    client: TestClient, engine: Engine
) -> None:
    """端末の控えが設定を変える前の案でも、外した相手の連絡は台帳に入らない。"""
    headers, version, _ = board_with_contacts(client, engine, [Part.CHILDCARE])
    res = client.post(
        "/api/board/branches",
        json={"version": version, "branch": branch().model_dump(mode="json")},
        headers=headers,
    )
    assert res.status_code == 201
    saved = res.json()
    assert part_names(saved["branches"][0]) == ["保育園・預け先", "病院"]
    sent_parts = {item["part"] for item in saved["items"] if item["id"].startswith("s-")}
    assert sent_parts == {"保育園・預け先", "病院"}


def test_own_home_takes_the_contacts_from_the_screen(client: TestClient) -> None:
    made = client.post(
        "/api/boards",
        json={"home": "own", "choices": {"parent": "母", "contacts": ["夫", "職場"]}},
    ).json()
    assert made["board"]["profile"]["contacts"] == ["家族", "職場"]
    assert made["board"]["profile"]["choices"]["contacts"] == ["夫", "職場"]


def test_setup_can_change_the_contacts(client: TestClient) -> None:
    made = client.post("/api/boards", json={"home": "own"}).json()
    headers = {"X-Family-Code": made["code"]}
    res = client.put(
        "/api/board/setup",
        json={"version": made["board"]["version"], "choices": {"contacts": []}},
        headers=headers,
    )
    assert res.status_code == 200
    assert res.json()["profile"]["contacts"] == []


def test_contacts_take_only_the_three_words(client: TestClient) -> None:
    """自由な文字を受けない。"""
    res = client.post("/api/boards", json={"home": "own", "choices": {"contacts": ["祖父母"]}})
    assert res.status_code == 422
