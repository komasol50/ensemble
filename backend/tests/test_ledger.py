import pytest
from pydantic import ValidationError
from sqlalchemy.engine import Engine

from ensemble.ledger import (
    Action,
    Certainty,
    Family,
    Item,
    Kind,
    Part,
    Profile,
    Status,
    new_family_code,
    progress_group,
    status_label,
)
from ensemble.store import (
    FamilyNotFound,
    VersionConflict,
    create_family,
    load_family,
    save_family,
)


def make_family() -> Family:
    return Family(code=new_family_code(), profile=Profile(name="家庭A"))


def make_item(**kwargs: object) -> Item:
    base = {"id": "i1", "kind": Kind.ACTION, "part": Part.FAMILY, "certainty": Certainty.LIKELY}
    return Item(**{**base, **kwargs})


def test_family_code_is_at_least_128_bits_and_unique() -> None:
    codes = {new_family_code() for _ in range(100)}
    assert len(codes) == 100
    # token_urlsafe は1文字6ビット
    assert all(len(c) * 6 >= 128 for c in codes)


def test_short_family_code_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Family(code="short", profile=Profile(name="家庭A"))


@pytest.mark.parametrize(
    "field,value",
    [
        ("kind", "メモ"),
        ("part", "パート"),
        ("certainty", "たぶん"),
        ("medical", "緊急"),
        ("status", "不可"),
    ],
)
def test_values_outside_enum_are_rejected(field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        make_item(**{field: value})


@pytest.mark.parametrize("missing", ["kind", "part", "certainty"])
def test_required_fields(missing: str) -> None:
    data = {"id": "i1", "kind": "行動", "part": "家族", "certainty": "見込み"}
    del data[missing]
    with pytest.raises(ValidationError):
        Item(**data)


def test_unknown_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make_item(priority="高")
    with pytest.raises(ValidationError):
        Action(part="家族", type="頼む", score=1)


def test_status_must_match_kind() -> None:
    assert make_item(status=Status.ASKED).status == Status.ASKED
    assert make_item(kind=Kind.QUESTION, status=Status.ANSWERED).status == Status.ANSWERED
    with pytest.raises(ValidationError):
        make_item(status=Status.ANSWERED)
    with pytest.raises(ValidationError):
        make_item(kind=Kind.QUESTION, status=Status.ASKED)
    with pytest.raises(ValidationError):
        make_item(kind=Kind.INFO, status=Status.ASKED)


def test_status_labels() -> None:
    assert status_label(Status.TO_ASK) == "未依頼"
    assert status_label(Status.ASKED) == "返答待ち"
    assert status_label(Status.OK_WITH_TIME, "18:30") == "18:30まで可"
    assert status_label(Status.DIFFICULT) == "難しい"


def test_progress_groups_keep_reply_and_done_apart() -> None:
    assert progress_group(Status.TO_ASK) == "未依頼"
    assert progress_group(Status.ASKED) == "返答待ち"
    for status in (Status.ACCEPTED, Status.DIFFICULT, Status.ON_HOLD, Status.OK_WITH_TIME):
        assert progress_group(status) == "返事あり"
    for status in (Status.DELIVERED, Status.DONE):
        assert progress_group(status) == "済"
    assert progress_group(Status.UNCONFIRMED) is None


def test_roundtrip_and_version_advances(engine: Engine) -> None:
    family = create_family(engine, make_family())
    family.items.append(make_item())
    saved = save_family(engine, family, expected_version=1)
    assert saved.version == 2
    loaded = load_family(engine, family.code)
    assert loaded.version == 2
    assert loaded.items[0].id == "i1"


def test_stale_version_is_rejected_and_nothing_is_lost(engine: Engine) -> None:
    family = create_family(engine, make_family())
    mother = load_family(engine, family.code)
    father = load_family(engine, family.code)

    mother.items.append(make_item(id="mother"))
    save_family(engine, mother, expected_version=mother.version)

    father.items.append(make_item(id="father"))
    with pytest.raises(VersionConflict):
        save_family(engine, father, expected_version=father.version)

    loaded = load_family(engine, family.code)
    assert [i.id for i in loaded.items] == ["mother"]
    assert loaded.version == 2


def test_missing_family(engine: Engine) -> None:
    with pytest.raises(FamilyNotFound):
        load_family(engine, "x" * 43)
    with pytest.raises(FamilyNotFound):
        save_family(engine, make_family(), expected_version=1)
