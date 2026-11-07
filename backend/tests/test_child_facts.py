"""子のあだ名と入院した日（①の先頭）。

合格の条件：あだ名はAIへの入力に生で出ない（［子］に伏せる）。入院した日はAIへの入力にも
日付の守りの照合先にも出ない。どちらも空なら台帳に書かない。profile と choices に入らない。
"""

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine

from ensemble.guards import check_branch, nickname_of, unmask_aliases, with_nickname
from ensemble.intake import build_intake_message
from ensemble.ledger import ActionType, Branch, Family, Item, Kind, Part, Certainty
from ensemble.main import create_app
from ensemble.presets import load_family_from_preset
from ensemble.retell import build_user_message, retell
from ensemble.store import load_family, stored

NICKNAME = "はるる"
ADMITTED = date(2026, 10, 30)
# 入院した日を、AIへの入力と日付の守りが拾いうる書き方で並べる。
ADMITTED_FORMS = ("2026-10-30", "10/30", "10月30日", "30日")
TODAY = date(2026, 11, 4)


class FakeClient:
    def __init__(self, outputs: list[Branch | None]) -> None:
        self.outputs = outputs
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
def client(engine: Engine, tmp_path: Path) -> TestClient:
    return TestClient(create_app(engine, dist=tmp_path, client_factory=lambda: None))


def own_board(client: TestClient, **extra: Any) -> dict:
    res = client.post(
        "/api/boards",
        json={"home": "own", "choices": {"parent": "母", "errand": "夫・パートナー"}, **extra},
    )
    assert res.status_code == 201
    return res.json()


def proposal_branch(draft: str) -> Branch:
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


def family_with_child() -> Family:
    """家庭Aに、あだ名と入院した日を入れ、あだ名を生で書いた記録を1つ足す。"""
    family = load_family_from_preset("home_a")
    family.aliases = with_nickname(family.aliases, NICKNAME)
    family.admitted_on = ADMITTED
    family.items.append(
        Item(
            id="n-1",
            kind=Kind.INFO,
            part=Part.FAMILY,
            certainty=Certainty.CONFIRMED,
            text_original=f"{NICKNAME}の着替えを持っていく",
            speaker="親",
        )
    )
    return family


# ---- 画面からの入り口 ----


def test_nickname_goes_to_aliases_not_to_profile_or_choices(
    client: TestClient, engine: Engine
) -> None:
    made = own_board(client, nickname=NICKNAME, admitted_on=ADMITTED.isoformat())
    family = load_family(engine, made["code"])
    assert family.aliases == {NICKNAME: "［子］"}
    assert family.admitted_on == ADMITTED
    profile = json.dumps(family.profile.model_dump(mode="json"), ensure_ascii=False)
    assert NICKNAME not in profile and "2026-10-30" not in profile
    assert "nickname" not in (family.profile.choices or {})
    # 画面には戻して出す。
    assert made["board"]["nickname"] == NICKNAME
    assert made["board"]["admitted_on"] == "2026-10-30"


def test_nickname_is_at_most_eight_characters(client: TestClient) -> None:
    res = client.post("/api/boards", json={"home": "own", "nickname": "あ" * 9})
    assert res.status_code == 422


def test_blank_nickname_registers_no_alias(client: TestClient, engine: Engine) -> None:
    """空の呼び名を登録すると、伏せる処理が全文を置き換えてしまう。"""
    made = own_board(client, nickname="  ")
    family = load_family(engine, made["code"])
    assert family.aliases == {}
    assert made["board"]["nickname"] is None
    assert with_nickname({}, "") == {}


def test_setup_keeps_or_clears_the_child_facts(client: TestClient, engine: Engine) -> None:
    made = own_board(client, nickname=NICKNAME, admitted_on=ADMITTED.isoformat())
    headers = {"X-Family-Code": made["code"]}
    # 送らなければ今のまま。
    kept = client.put(
        "/api/board/setup",
        json={"version": made["board"]["version"], "choices": {"errand": "いない"}},
        headers=headers,
    ).json()
    assert kept["nickname"] == NICKNAME and kept["admitted_on"] == "2026-10-30"
    # 直せる。前のあだ名は残さない。
    changed = client.put(
        "/api/board/setup",
        json={
            "version": kept["version"],
            "choices": {"errand": "いない"},
            "nickname": "ぴよ",
            "admitted_on": "2026-11-01",
        },
        headers=headers,
    ).json()
    assert changed["nickname"] == "ぴよ" and changed["admitted_on"] == "2026-11-01"
    assert load_family(engine, made["code"]).aliases == {"ぴよ": "［子］"}
    # 空にすると消える。
    cleared = client.put(
        "/api/board/setup",
        json={
            "version": changed["version"],
            "choices": {"errand": "いない"},
            "nickname": "",
            "admitted_on": None,
        },
        headers=headers,
    ).json()
    assert cleared["nickname"] is None and cleared["admitted_on"] is None
    assert load_family(engine, made["code"]).aliases == {}


def test_without_nickname_the_child_is_shown_as_okosan() -> None:
    assert unmask_aliases("［子］の着替え", {}) == "お子さんの着替え"
    assert unmask_aliases("［子］の着替え", {"ぴよ": "［子］"}) == "ぴよの着替え"
    assert nickname_of({"田中部長": "［上司］"}) is None


# ---- (a) あだ名はAIへの入力に出ない ----


def test_nickname_is_masked_in_the_proposal_input() -> None:
    family = family_with_child()
    sent = build_user_message(family, "a-07", "result", today=TODAY)
    assert NICKNAME not in sent
    assert "［子］の着替えを持っていく" in sent


def test_nickname_is_masked_in_the_intake_input() -> None:
    family = family_with_child()
    sent = build_intake_message(family, f"{NICKNAME}の採血はだめでした", today=TODAY.isoformat())
    assert NICKNAME not in sent
    assert "［子］の採血はだめでした" in sent


def test_nickname_is_masked_in_what_retell_sends_even_on_the_second_try() -> None:
    """呼び直しの文（前回の出力とエラー）も含めて、AIへ渡る文の全部にあだ名が無い。"""
    family = family_with_child()
    # 1回目は守りで落ちる案（根拠の原文に無い日付）、2回目は通る案。
    fake = FakeClient(
        [
            proposal_branch("10/30の延長の空きを確認させてください"),
            proposal_branch("木曜は取り消し、火曜の延長の空きを確認させてください"),
        ]
    )
    retell(fake, family, "a-07", "result", today=TODAY)
    assert len(fake.calls) == 2
    for call in fake.calls:
        sent = json.dumps(call, ensure_ascii=False, default=str)
        assert NICKNAME not in sent


# ---- (b) 入院した日はAIへの入力にも日付の照合先にも出ない ----


@pytest.mark.parametrize("form", ADMITTED_FORMS)
def test_admitted_day_is_not_in_the_ai_inputs(form: str) -> None:
    family = family_with_child()
    proposal = build_user_message(family, "a-07", "result", today=TODAY)
    intake = build_intake_message(family, "採血はだめでした", today=TODAY.isoformat())
    assert form not in proposal
    assert form not in intake


def test_admitted_day_does_not_open_the_date_guard() -> None:
    """入院した日を下書きに書いても、根拠の原文に無い日付として止まる。"""
    family = family_with_child()
    good = proposal_branch("木曜は取り消し、火曜の延長の空きを確認させてください")
    assert check_branch(good, family) == []
    invented = proposal_branch("10/30から入院中です。火曜の延長の空きを確認させてください")
    assert check_branch(invented, family)
    family.admitted_on = None
    assert check_branch(invented, family)


# ---- (c) 空なら台帳に書かない ----


def test_empty_child_facts_are_not_stored(client: TestClient, engine: Engine) -> None:
    family = load_family_from_preset("home_a")
    data = stored(family)
    assert "admitted_on" not in data
    assert data["aliases"] == {}
    family.admitted_on = ADMITTED
    assert stored(family)["admitted_on"] == "2026-10-30"
    # 画面から作った家庭でも同じ。
    made = own_board(client)
    assert load_family(engine, made["code"]).admitted_on is None
    assert made["board"]["nickname"] is None and made["board"]["admitted_on"] is None


# ---- ①の値（B9・B10） ----


def test_home_centered_work_is_a_choice(client: TestClient) -> None:
    made = client.post(
        "/api/boards", json={"home": "own", "choices": {"work": "家のことが中心"}}
    ).json()
    assert made["board"]["profile"]["choices"]["work"] == "家のことが中心"


def test_retired_questions_are_read_but_not_written_for_new_homes(client: TestClient) -> None:
    """外した2問：古い台帳の答えは受け付け、新しい家庭の choices には書かない。"""
    new = client.post("/api/boards", json={"home": "own", "choices": {"parent": "母"}}).json()
    choices = new["board"]["profile"]["choices"]
    assert "adult" not in choices and "child" not in choices
    old = client.post(
        "/api/boards", json={"home": "own", "choices": {"adult": "いる", "child": "いない"}}
    ).json()
    assert old["board"]["profile"]["choices"]["adult"] == "いる"
    assert old["board"]["profile"]["choices"]["child"] == "いない"
