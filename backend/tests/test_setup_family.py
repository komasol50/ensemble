"""①の家族の問い（2026-11-08 PO、夫の初見）：入院している子・家にいるきょうだい・動ける時間帯。

合格の条件：答えは全部選ぶ形で、自由な文字が入らない。台帳の欄は足さず、今ある older_child と
availability の文にする。前の画面の答え（school・errand_time）は、古い台帳のために受け付ける。
画面の試す家庭の初めの内容（Menu.tsx）とサーバーの TRIAL_CHOICES が同じ。
"""

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine

from ensemble.ledger import Profile
from ensemble.main import create_app
from ensemble.presets import TRIAL_CHOICES, profile_from_choices

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def client(engine: Engine, tmp_path: Path) -> TestClient:
    return TestClient(create_app(engine, dist=tmp_path))


def own(client: TestClient, choices: dict) -> dict:
    res = client.post("/api/boards", json={"home": "own", "choices": choices})
    assert res.status_code == 201, res.text
    return res.json()


def test_child_and_siblings_become_the_older_child_text() -> None:
    profile = profile_from_choices(
        {
            "child_age": 5,
            "child_sex": "女の子",
            "child_school": "保育園・幼稚園",
            "siblings": [
                {"age": 8, "sex": "男の子", "school": "学校"},
                {"age": 3, "sex": "答えない", "school": "保育園・幼稚園"},
            ],
        }
    )
    assert profile.older_child == (
        "入院している子：5歳・女の子・保育園・幼稚園に通う。"
        "家にいるきょうだい：2人（8歳・男の子・学校、3歳・保育園・幼稚園）"
    )


def test_unknown_parts_are_left_out() -> None:
    profile = profile_from_choices(
        {
            "child_age": 0,
            "child_sex": "答えない",
            "child_school": None,
            "siblings": [{"age": None, "sex": None, "school": "なし"}, {"age": None}],
        }
    )
    assert (
        profile.older_child
        == "入院している子：0歳。家にいるきょうだい：2人（通園・通学なし、未設定）"
    )
    only_count = profile_from_choices({"siblings": [{}, {}, {}]})
    assert only_count.older_child == "家にいるきょうだい：3人"
    nothing = profile_from_choices(
        {"child_age": None, "child_sex": "答えない", "child_school": None, "siblings": []}
    )
    assert nothing.older_child == ""


def test_times_are_joined_in_screen_order() -> None:
    picked = profile_from_choices(
        {"errand": "妻・パートナー", "errand_times": ["休みの日", "平日の夜"]}
    )
    assert [person.model_dump() for person in picked.errand_people] == [
        {"label": "妻・パートナー", "availability": "平日の夜・休みの日"}
    ]
    unset = profile_from_choices({"errand": "祖父母", "errand_times": []})
    assert unset.errand_people[0].availability == "動ける時間は未設定"


def test_old_answers_still_make_the_old_text() -> None:
    """前の画面の答えだけの台帳（と古い画面からの保存）は、今までと同じ文になる。"""
    profile = profile_from_choices(
        {"errand": "夫・パートナー", "errand_time": "平日の夜と朝", "school": "保育園"}
    )
    assert profile.older_child == "上の子は保育園"
    assert profile.errand_people[0].availability == "平日の夜と朝"


def test_new_answers_win_over_old_ones() -> None:
    """新しい答えを送ったら、残っていた古い答えは文に使わない（見えない答えで文を変えない）。"""
    profile = profile_from_choices(
        {
            "errand": "夫・パートナー",
            "errand_time": "平日の夜と朝",
            "errand_times": [],
            "school": "保育園",
            "siblings": [],
        }
    )
    assert profile.older_child == ""
    assert profile.errand_people[0].availability == "動ける時間は未設定"


def test_the_profile_has_no_new_fields() -> None:
    """台帳の形は契約。新しい答えは choices と、今ある欄の文にだけ入る。"""
    assert set(Profile.model_fields) == {
        "name",
        "bedside_others",
        "errand_people",
        "older_child",
        "work",
        "grandparents",
        "supports",
        "configured",
        "choices",
        "contacts",
    }


def test_own_home_takes_the_new_questions(client: TestClient) -> None:
    choices = {
        "parent": "父",
        "work": "会社員",
        "errand": "妻・パートナー",
        "bedside": "いない",
        "supports": [],
        "no_support": False,
        "contacts": ["夫", "保育園", "職場"],
        "child_age": 4,
        "child_sex": "男の子",
        "child_school": "保育園・幼稚園",
        "siblings": [{"age": 7, "sex": "女の子", "school": "学校"}],
        "errand_times": ["平日の夜", "休みの日", "平日の夜"],
    }
    profile = own(client, choices)["board"]["profile"]
    assert profile["older_child"] == (
        "入院している子：4歳・男の子・保育園・幼稚園に通う。家にいるきょうだい：1人（7歳・女の子・学校）"
    )
    assert profile["errand_people"] == [
        {"label": "妻・パートナー", "availability": "平日の夜・休みの日"}
    ]
    # 外した問いは、答えが無ければ書かない。同じ時間帯は1つにする。
    assert "school" not in profile["choices"] and "errand_time" not in profile["choices"]
    assert profile["choices"]["errand_times"] == ["平日の夜", "休みの日"]
    assert profile["choices"]["siblings"] == [{"age": 7, "sex": "女の子", "school": "学校"}]


def test_old_screen_choices_are_still_accepted(client: TestClient) -> None:
    profile = own(
        client,
        {
            "errand": "祖父母",
            "errand_time": "休みの日だけ",
            "school": "学校",
            "supports": ["ファミサポ"],
        },
    )["board"]["profile"]
    assert profile["older_child"] == "上の子は学校"
    assert profile["errand_people"][0]["availability"] == "休みの日だけ"
    assert profile["supports"][0]["name"] == "ファミサポ"
    # 送られなかった新しい問いは choices に書かない（古い答えで文を作ったことが分かる形）。
    assert "siblings" not in profile["choices"] and "errand_times" not in profile["choices"]


@pytest.mark.parametrize(
    "choices",
    [
        {"siblings": [{"age": 3, "name": "たろう"}]},
        {"siblings": [{"age": 19}]},
        {"siblings": [{"age": "3"}]},
        {"siblings": [{"sex": "その他"}]},
        {"siblings": [{"school": "保育園"}]},
        {"siblings": [{}] * 21},
        {"child_age": 19},
        {"child_age": -1},
        {"child_age": "5"},
        {"child_age": True},
        {"child_sex": "男"},
        {"child_school": "小学校"},
        {"child_name": "はるる"},
        {"errand_times": ["夜"]},
        {"errand": "パートナー"},
    ],
)
def test_only_the_choices_on_screen_are_accepted(client: TestClient, choices: dict) -> None:
    res = client.post("/api/boards", json={"home": "own", "choices": choices})
    assert res.status_code == 422


def test_twenty_siblings_are_accepted(client: TestClient) -> None:
    profile = own(client, {"siblings": [{}] * 20})["board"]["profile"]
    assert profile["older_child"] == "家にいるきょうだい：20人"


def test_setup_change_rewrites_the_text(client: TestClient) -> None:
    made = own(client, {"errand": "夫・パートナー", "siblings": [], "errand_times": []})
    res = client.put(
        "/api/board/setup",
        json={
            "version": made["board"]["version"],
            "choices": {
                "errand": "妻・パートナー",
                "siblings": [{"age": 10, "sex": "答えない", "school": "学校"}],
                "errand_times": ["平日の朝"],
            },
        },
        headers={"X-Family-Code": made["code"]},
    )
    profile = res.json()["profile"]
    assert profile["older_child"] == "家にいるきょうだい：1人（10歳・学校）"
    assert profile["errand_people"] == [{"label": "妻・パートナー", "availability": "平日の朝"}]


def test_saving_trial_home_a_as_shown_keeps_the_preset(client: TestClient) -> None:
    preset = client.post("/api/boards", json={"home": "home_a"}).json()
    before = preset["board"]["profile"]
    res = client.put(
        "/api/board/setup",
        json={"version": preset["board"]["version"], "choices": TRIAL_CHOICES["家庭A"]},
        headers={"X-Family-Code": preset["code"]},
    )
    after = res.json()["profile"]
    for field in ("errand_people", "older_child", "work", "grandparents", "supports"):
        assert after[field] == before[field], field
    assert after.get("choices") is None


def test_changing_siblings_of_a_trial_home_changes_only_the_child_text(client: TestClient) -> None:
    preset = client.post("/api/boards", json={"home": "home_a"}).json()
    before = preset["board"]["profile"]
    choices = {**TRIAL_CHOICES["家庭A"], "siblings": []}
    after = client.put(
        "/api/board/setup",
        json={"version": preset["board"]["version"], "choices": choices},
        headers={"X-Family-Code": preset["code"]},
    ).json()["profile"]
    assert after["older_child"] == ""
    assert after["errand_people"] == before["errand_people"]
    assert after["supports"] == before["supports"]


def _menu_trial_choices() -> dict:
    """Menu.tsx の TRIAL_CHOICES を読む（TypeScript の書き方を JSON に直す）。"""
    source = (ROOT / "frontend/src/Menu.tsx").read_text(encoding="utf-8")
    block = re.search(
        r"const TRIAL_CHOICES: Record<string, SetupChoices> = (\{.*?\n\})\n", source, re.S
    )[1]
    block = re.sub(r"//[^\n]*", "", block)
    block = re.sub(r"([{,]\s*)([^\s:'\"{},\[\]]+):", r'\1"\2":', block)
    block = block.replace("'", '"')
    block = re.sub(r",(\s*[}\]])", r"\1", block)
    return json.loads(block)


def test_screen_and_server_start_trial_homes_from_the_same_choices() -> None:
    assert _menu_trial_choices() == TRIAL_CHOICES
