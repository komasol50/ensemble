"""守りの関数ごとに、良い例と悪い例。悪い例は必ず止まること。"""

import pytest

from ensemble.guards import (
    INPUT_MAX_CHARS,
    MEDICAL_FIXED_TEXT,
    check_action,
    check_action_type_for_certainty,
    check_dates,
    check_family_request,
    check_input_length,
    check_medical_words,
    check_no_medical_ask_basis,
    extract_date_tokens,
    is_savable,
    is_third_party,
    lower_certainty,
    mask_aliases,
    medical_reply,
    resolve_basis,
    strip_third_party,
    unmask_aliases,
)
from ensemble.ledger import (
    Action,
    ActionType,
    Certainty,
    ErrandPerson,
    Item,
    Kind,
    Medical,
    Part,
    Profile,
)

DOCTOR = (
    "先生：明日の採血しだい。よければ木曜に帰れるかも。"
    "だめなら月曜にもう一回とって、それで大丈夫なら火曜かな"
)


def item(**kwargs: object) -> Item:
    base = {"id": "i1", "kind": Kind.INFO, "part": Part.HOSPITAL, "certainty": Certainty.LIKELY}
    return Item(**{**base, **kwargs})


def action(**kwargs: object) -> Action:
    base = {"part": Part.CHILDCARE, "type": ActionType.CONFIRM, "basis_ids": ["i1"]}
    return Action(**{**base, **kwargs})


HOME_A = Profile(name="家庭A", errand_people=[ErrandPerson(label="夫", availability="夜と朝")])
HOME_NO_ERRAND = Profile(name="頼める人なし")


# ---- 医療の線 ----


def test_fixed_text_is_exact() -> None:
    assert MEDICAL_FIXED_TEXT == (
        "体調や治療のことには答えません。急ぐときは、今すぐナースコールを押してください。"
        "急がないことは、病棟のスタッフに直接確認してください。"
    )


@pytest.mark.parametrize(
    "text", ["この薬、減らしてもいい？", "すごく苦しそう、どうしよう", "退院はいつ？"]
)
def test_every_medical_ask_gets_the_same_text(text: str) -> None:
    assert medical_reply(item(text_original=text, medical=Medical.ASK)) == MEDICAL_FIXED_TEXT


def test_non_ask_gets_no_fixed_text() -> None:
    assert medical_reply(item(medical=Medical.INFO)) is None
    assert medical_reply(item(medical=Medical.NONE)) is None


@pytest.mark.parametrize(
    "draft",
    [
        "薬を減らしてください",
        "10mgにしてください",
        "投与を中止します",
        "熱が38度なら連絡します",
        "量を増やす",
    ],
)
def test_medical_words_stop_without_doctor_basis(draft: str) -> None:
    assert check_medical_words(draft, [item(medical=Medical.NONE)])


def test_medical_words_pass_with_doctor_basis() -> None:
    assert check_medical_words("薬の説明を受けました", [item(medical=Medical.INFO)]) == []


@pytest.mark.parametrize(
    "draft", ["お薬手帳を持ってきて", "薬局で受け取ってほしい", "着替えを3日分お願い", None, ""]
)
def test_life_errands_are_not_stopped(draft: str | None) -> None:
    assert check_medical_words(draft, [item(medical=Medical.NONE)]) == []


def test_medical_ask_cannot_be_basis() -> None:
    assert check_no_medical_ask_basis([item(medical=Medical.ASK)])
    assert check_no_medical_ask_basis([item(medical=Medical.INFO)]) == []


# ---- 日付の守り ----


@pytest.mark.parametrize(
    "text,expected",
    [
        ("木曜に帰れるかも", {"木曜"}),
        ("木曜日に", {"木曜"}),
        ("（木）の延長", {"木曜"}),
        ("木の延長の確認", {"木曜"}),
        ("月・火の空きを確認", {"月曜", "火曜"}),
        ("保育園は水の延長", {"水曜"}),
        ("迎えは金・土で", {"金曜", "土曜"}),
        ("11月3日に", {"11/3"}),
        ("11/3に", {"11/3"}),
        ("3日に", {"3日"}),
        ("18:30まで", {"18:30"}),
        ("18時半まで", {"18:30"}),
        ("１８：３０まで", {"18:30"}),
        ("9時に", {"9:00"}),
        ("明日の採血", {"明日"}),
        ("再来週に", {"再来週"}),
    ],
)
def test_extract_date_tokens(text: str, expected: set[str]) -> None:
    assert extract_date_tokens(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "着替えを3日分",
        "2時間かかります",
        "お金の話",
        "その日のうちに",
        "毎月の支払い",
        "10日間の入院",
    ],
)
def test_not_dates(text: str) -> None:
    assert extract_date_tokens(text) == set()


def test_date_in_basis_passes() -> None:
    basis = [item(text_original=DOCTOR)]
    assert check_dates("月曜と火曜の延長に空きはありますか", basis) == []
    assert check_dates("月・火の空きを確認させてください", basis) == []


@pytest.mark.parametrize(
    "draft",
    [
        "水曜に退院です",
        "18:30にお願いします",
        "11月5日に退院します",
        "来週に伸びました",
        "今夜お願い",
    ],
)
def test_invented_date_stops(draft: str) -> None:
    assert check_dates(draft, [item(text_original=DOCTOR)])


def test_third_party_summary_is_not_a_date_source() -> None:
    basis = [item(text_original=None, task_summary="木曜の会議をどうするか")]
    assert check_dates("木曜の会議", basis)


def test_no_dates_in_draft_passes() -> None:
    assert check_dates("着替え3日分をお願い", [item(text_original=DOCTOR)]) == []
    assert check_dates(None, []) == []


# ---- 根拠ID ----


def test_basis_in_ledger_passes() -> None:
    basis, violations = resolve_basis(action(), [item()])
    assert [b.id for b in basis] == ["i1"]
    assert violations == []


def test_unknown_basis_stops() -> None:
    assert resolve_basis(action(basis_ids=["i1", "ghost"]), [item()])[1]


def test_empty_basis_stops() -> None:
    assert resolve_basis(action(basis_ids=[]), [item()])[1]


def test_nothing_needs_no_basis() -> None:
    assert resolve_basis(action(type=ActionType.NOTHING, basis_ids=[]), [])[1] == []


def test_unknown_release_stops() -> None:
    assert resolve_basis(action(release=["ghost"]), [item()])[1]
    assert resolve_basis(action(release=["i1"]), [item()])[1] == []


# ---- 見込みの案 ----


@pytest.mark.parametrize("type_", [ActionType.REQUEST, ActionType.TELL])
def test_likely_only_basis_stops_firm_actions(type_: ActionType) -> None:
    assert check_action_type_for_certainty(action(type=type_), [item()])


@pytest.mark.parametrize("type_", [ActionType.CONFIRM, ActionType.CHECK_AVAILABILITY])
def test_likely_only_basis_allows_checking(type_: ActionType) -> None:
    assert check_action_type_for_certainty(action(type=type_), [item()]) == []


def test_confirmed_fact_in_basis_allows_request() -> None:
    basis = [item(), item(id="i2", certainty=Certainty.CONFIRMED)]
    assert check_action_type_for_certainty(action(type=ActionType.REQUEST), basis) == []


# ---- 頼めない家庭 ----


@pytest.mark.parametrize(
    "type_", [ActionType.REQUEST, ActionType.CONFIRM, ActionType.CHECK_AVAILABILITY]
)
def test_no_errand_person_stops_family_request(type_: ActionType) -> None:
    assert check_family_request(action(part=Part.FAMILY, type=type_), HOME_NO_ERRAND)


def test_errand_person_allows_family_request() -> None:
    assert check_family_request(action(part=Part.FAMILY, type=ActionType.REQUEST), HOME_A) == []


def test_no_errand_person_allows_support_and_telling_family() -> None:
    assert check_family_request(action(part=Part.SUPPORT), HOME_NO_ERRAND) == []
    assert (
        check_family_request(action(part=Part.FAMILY, type=ActionType.TELL), HOME_NO_ERRAND) == []
    )


# ---- 全部まとめて ----


def test_check_action_good() -> None:
    good = action(
        type=ActionType.CHECK_AVAILABILITY,
        draft="月曜と火曜の延長に空きはありますか",
        basis_ids=["i1"],
    )
    assert check_action(good, [item(text_original=DOCTOR, medical=Medical.INFO)], HOME_A) == []


@pytest.mark.parametrize(
    "bad",
    [
        {"basis_ids": ["ghost"]},
        {"draft": "水曜の延長に空きはありますか"},
        {"draft": "薬を減らします", "medical_basis": Medical.NONE},
        {"type": ActionType.REQUEST},
    ],
)
def test_check_action_bad_always_stops(bad: dict) -> None:
    bad = dict(bad)
    medical = bad.pop("medical_basis", Medical.INFO)
    assert check_action(action(**bad), [item(text_original=DOCTOR, medical=medical)], HOME_A)


# ---- 確かさを下げる ----


@pytest.mark.parametrize(
    "text",
    [
        "木曜に帰れるかも",
        "火曜かな",
        "採血しだいで木曜",
        "木曜の予定です",
        "よければ木曜なら",
        "木曜の見込み",
        "そのうち帰れます",
    ],
)
def test_confirmed_is_lowered(text: str) -> None:
    lowered = lower_certainty(item(text_original=text, certainty=Certainty.CONFIRMED))
    assert lowered.certainty == Certainty.LIKELY


def test_confirmed_with_date_and_no_condition_stays() -> None:
    kept = item(text_original="今日の採血はだめでした", certainty=Certainty.CONFIRMED)
    assert lower_certainty(kept).certainty == Certainty.CONFIRMED


def test_certainty_is_never_raised() -> None:
    guess = item(text_original="今日の採血はだめでした", certainty=Certainty.GUESS)
    assert lower_certainty(guess).certainty == Certainty.GUESS


# ---- 保存の条件 ----


def test_savable() -> None:
    assert is_savable({"kind": "行動", "part": "家族", "certainty": "見込み"})


@pytest.mark.parametrize("field", ["kind", "part", "certainty"])
@pytest.mark.parametrize("empty", [None, ""])
def test_blank_required_field_is_not_savable(field: str, empty: str | None) -> None:
    raw = {"kind": "行動", "part": "家族", "certainty": "見込み", field: empty}
    assert not is_savable(raw)


# ---- 第三者の文 ----


@pytest.mark.parametrize(
    "part,speaker",
    [
        (Part.WORK, "上司"),
        (Part.CHILDCARE, "保育園"),
        (Part.SUPPORT, "ファミサポ"),
        (Part.WORK, None),
    ],
)
def test_third_party_original_is_dropped(part: Part, speaker: str | None) -> None:
    source = item(
        part=part, speaker=speaker, text_original="木曜の会議どうする？", task_summary="会議の連絡"
    )
    assert is_third_party(source)
    saved = strip_third_party(source, "2026-11-02T10:00:00+09:00")
    assert saved.text_original is None
    assert saved.task_summary == "会議の連絡"
    assert saved.recorded_at == "2026-11-02T10:00:00+09:00"


@pytest.mark.parametrize(
    "part,speaker", [(Part.HOSPITAL, "医師"), (Part.FAMILY, "夫"), (Part.WORK, "母")]
)
def test_doctor_family_and_parent_keep_original(part: Part, speaker: str) -> None:
    source = item(part=part, speaker=speaker, text_original="原文")
    assert strip_third_party(source, "t").text_original == "原文"


def test_third_party_without_summary_is_refused() -> None:
    with pytest.raises(ValueError):
        strip_third_party(item(part=Part.WORK, speaker="上司", text_original="原文"), "t")


# ---- 呼び名 ----


ALIASES = {"はなこ": "［子］", "田中部長": "［上司］", "田中": "［上司の名字］"}


def test_aliases_are_masked_longest_first_and_restored() -> None:
    text = "はなこのことで田中部長に連絡。田中さんにも。"
    masked = mask_aliases(text, ALIASES)
    assert "はなこ" not in masked
    assert "田中" not in masked
    assert masked.startswith("［子］のことで［上司］に連絡")
    assert unmask_aliases(masked, ALIASES) == text


def test_text_without_aliases_is_unchanged() -> None:
    assert mask_aliases("木曜に帰れるかも", ALIASES) == "木曜に帰れるかも"


# ---- 入力の長さ ----


def test_input_length() -> None:
    assert check_input_length("あ" * INPUT_MAX_CHARS)
    assert not check_input_length("あ" * (INPUT_MAX_CHARS + 1))
