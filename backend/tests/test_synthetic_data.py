"""合成データが型に通り、台本と仕様の条件を満たすこと。"""

import json
from pathlib import Path

import pytest

from ensemble.config import ROOT
from ensemble.guards import is_third_party, lower_certainty
from ensemble.ledger import Certainty, Family, Medical, Part, Status
from ensemble.presets import DATA_DIR, HOMES, load_family_from_preset

DOCTOR = (
    "先生：明日の採血しだい。よければ木曜に帰れるかも。"
    "だめなら月曜にもう一回とって、それで大丈夫なら火曜かな"
)
# ルールの原文にある「交代」は作り手の表の原文で、③④は使わない（画面に出さない）。
BANNED = (
    "リレー",
    "バトン",
    "申し送り",
    "シフト",
    "分け合う",
    "チーム",
    "家族みんなで",
    "寄り添う",
    "抱え込ませない",
    "予測",
    "AIが判断",
    "相談できる",
    "不可",
)


def cases() -> list[dict]:
    return json.loads((ROOT / "evals" / "gate1" / "cases.json").read_text(encoding="utf-8"))[
        "cases"
    ]


def ledger(case: dict) -> Family:
    return load_family_from_preset(case["home"], extra_items=case["extra_items"])


@pytest.mark.parametrize("home", HOMES)
def test_presets_and_initial_pass_the_schema(home: str) -> None:
    family = load_family_from_preset(home)
    ids = [item.id for item in family.items]
    assert len(ids) == len(set(ids))
    assert any(item.text_original == DOCTOR for item in family.items)


@pytest.mark.parametrize("home", HOMES)
def test_rules_match_the_decided_set(home: str) -> None:
    rules = {rule.id: rule.active for rule in load_family_from_preset(home).rules}
    assert rules == {"1": False, "2": True, "3": False, "4": False, "5": True}


@pytest.mark.parametrize("home", HOMES)
def test_third_party_originals_are_not_kept(home: str) -> None:
    for item in load_family_from_preset(home).items:
        if is_third_party(item):
            assert item.text_original is None, item.id


@pytest.mark.parametrize("home", HOMES)
def test_initial_items_already_satisfy_lower_certainty(home: str) -> None:
    for item in load_family_from_preset(home).items:
        assert lower_certainty(item).certainty == item.certainty, item.id


def test_home_a_matches_the_script_initial_state() -> None:
    items = load_family_from_preset("home_a").items
    by_summary = {item.task_summary: item for item in items}
    assert by_summary["退院の時刻"].status == Status.UNCONFIRMED
    assert by_summary["持ち物の確認"].status == Status.TO_ASK
    assert by_summary["洗濯物の持ち帰り"].status == Status.ACCEPTED
    assert by_summary["充電器"].status == Status.DONE
    assert by_summary["木の延長の空きを確認"].status == Status.ASKED
    assert by_summary["木の延長の空きを確認"].part == Part.CHILDCARE
    assert by_summary["上司から、木曜の会議をどうするかの連絡"].status == Status.TO_ASK


def test_home_b_has_no_one_else_at_the_bedside_and_only_grandparents_for_errands() -> None:
    """2026-11-07 本人の決定：夫に頼めず、用事を頼める人は祖父母だけ。支援は無い。上の子は小学校（2026-11-08 PO）。"""
    family = load_family_from_preset("home_b")
    assert family.profile.bedside_others == []
    assert [person.label for person in family.profile.errand_people] == ["祖父母"]
    assert (
        family.profile.supports == []
        and family.profile.older_child == "家にいるきょうだい：1人（学校）"
    )
    assert all(item.part not in ("保育園・預け先", "支援") for item in family.items)


def test_gate1_has_two_cases_for_each_home() -> None:
    all_cases = cases()
    assert len(all_cases) == 4
    assert sum(case["home"] == "home_a" for case in all_cases) == 2
    assert sum(case["home"] == "home_b" for case in all_cases) == 2
    assert any("祖父母" in case["look_for"] for case in all_cases if case["home"] == "home_b")


@pytest.mark.parametrize("case", cases(), ids=lambda c: c["id"])
def test_gate1_cases_pass_the_schema_and_point_to_real_items(case: dict) -> None:
    family = ledger(case)
    ids = [item.id for item in family.items]
    assert len(ids) == len(set(ids))
    assert case["trigger_item_id"] in ids


@pytest.mark.parametrize(
    "case", [c for c in cases() if c["stage"] == "result"], ids=lambda c: c["id"]
)
def test_result_comes_from_the_parents_input(case: dict) -> None:
    trigger = next(i for i in ledger(case).items if i.id == case["trigger_item_id"])
    assert trigger.speaker == "親"
    assert trigger.certainty == Certainty.CONFIRMED
    assert trigger.medical == Medical.INFO


@pytest.mark.parametrize(
    "path",
    [
        *sorted((DATA_DIR / "presets").glob("*.json")),
        *sorted((DATA_DIR / "initial").glob("*.json")),
        ROOT / "evals" / "gate1" / "cases.json",
    ],
    ids=lambda p: p.name,
)
def test_no_banned_words(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert [word for word in BANNED if word in text] == []


def test_shoot_state_has_no_doctor_words_and_no_proposal() -> None:
    """撮影は台本の順に貼って案を出す。貼る前から案があると、作りものになる。"""
    family = load_family_from_preset("shoot_a")
    assert family.branches == []
    assert not any(item.medical == Medical.INFO for item in family.items)
    # 図の筋書き（11/9）：病院の項目は撮影で貼る。前からの夫・職場・保育園の用事と、親の一言だけ。
    assert {item.part for item in family.items} == {
        Part.FAMILY,
        Part.WORK,
        Part.CHILDCARE,
        Part.PARENT,
    }
