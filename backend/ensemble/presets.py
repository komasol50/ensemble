"""合成データ（プリセットと初期状態）を読み、台帳にする。"""

import json
from datetime import datetime, timedelta
from pathlib import Path

from .config import ROOT
from .contacts import contacts_from_choices
from .ledger import ErrandPerson, Family, Item, Profile, Rule, Support, new_family_code

DATA_DIR = ROOT / "data"
HOMES = ("home_a", "home_b")
SEED_GAP = timedelta(minutes=3)


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def spaced_records(items: list[Item], now: str) -> list[Item]:
    """初期データの記録の時刻を、今を最後に数分ずつさかのぼらせる。

    週の表は記録の日から曜日を置くので、日はまたがない。先に起きた順は初期データのまま。
    """
    latest = datetime.fromisoformat(now)
    day_start = latest.replace(hour=0, minute=0, second=0, microsecond=0)
    # 時刻の無い項目は、初期データの中で一番新しいものとして扱う。
    ordered = sorted(items, key=lambda item: (item.recorded_at is None, item.recorded_at or ""))
    steps_back = {item.id: len(ordered) - 1 - place for place, item in enumerate(ordered)}
    return [
        item.model_copy(
            update={
                "recorded_at": max(latest - SEED_GAP * steps_back[item.id], day_start).isoformat(
                    timespec="seconds"
                )
            }
        )
        for item in items
    ]


def load_family_from_preset(home: str, *, extra_items: list[dict] | None = None) -> Family:
    """プリセットと初期状態から台帳を作る。家族コードは毎回新しく作る。"""
    preset = _read(DATA_DIR / "presets" / f"{home}.json")
    initial = _read(DATA_DIR / "initial" / f"{home}.json")
    return Family(
        code=new_family_code(),
        profile=Profile.model_validate(preset["profile"]),
        aliases=preset["aliases"],
        rules=[Rule.model_validate(rule) for rule in preset["rules"]],
        items=[Item.model_validate(item) for item in initial["items"] + (extra_items or [])],
    )


OWN_HOME_NAME = "あなたの家庭"
SUPPORT_DETAILS = {
    "延長保育": "空きは日ごとに確認",
    "ファミサポ": "登録済み。空きは都度確認",
    "一時預かり": "登録済み。空きは都度確認",
}


# 頼める人が動ける時間帯（いくつでも）。並びは画面の並び。
ERRAND_TIME_ORDER = ("平日の朝", "平日の日中", "平日の夜", "休みの日")
NO_ERRAND_TIME = "動ける時間は未設定"
# 子の事情の文に入れる答え（2026-11-08 PO）。どれかがあれば、古い「上の子は◯◯」は使わない。
CHILD_KEYS = ("child_age", "child_sex", "child_school", "siblings")


def _child_parts(age: object, sex: object, school: object, *, attending: bool) -> list[str]:
    """子1人の答えを短い語に。分からない答えと「答えない」は書かない。"""
    parts = [f"{age}歳"] if isinstance(age, int) and not isinstance(age, bool) else []
    if sex in ("男の子", "女の子"):
        parts.append(str(sex))
    if school == "なし":
        parts.append("通園・通学なし")
    elif school in ("保育園・幼稚園", "学校"):
        parts.append(f"{school}に通う" if attending else str(school))
    return parts


def child_text(choices: dict) -> str:
    """入院している子と家にいるきょうだいの文。全部選ぶ形の答えから作り、自由な文字は入らない。

    例：「入院している子：5歳・女の子・保育園・幼稚園に通う。
    家にいるきょうだい：2人（8歳・男の子・学校、3歳・保育園・幼稚園）」（実際は1行）
    """
    sentences = []
    child = _child_parts(
        choices.get("child_age"),
        choices.get("child_sex"),
        choices.get("child_school"),
        attending=True,
    )
    if child:
        sentences.append("入院している子：" + "・".join(child))
    siblings = choices.get("siblings") or []
    if siblings:
        each = [
            "・".join(
                _child_parts(
                    sibling.get("age"), sibling.get("sex"), sibling.get("school"), attending=False
                )
            )
            or "未設定"
            for sibling in siblings
        ]
        known = any(entry != "未設定" for entry in each)
        detail = f"（{'、'.join(each)}）" if known else ""
        sentences.append(f"家にいるきょうだい：{len(siblings)}人{detail}")
    return "。".join(sentences)


def availability_text(choices: dict) -> str:
    """頼める人が動ける時間帯の文。前の画面の1択（errand_time）は、新しい答えが無いときだけ使う。"""
    if "errand_times" in choices:
        picked = set(choices.get("errand_times") or [])
        times = [slot for slot in ERRAND_TIME_ORDER if slot in picked]
        return "・".join(times) or NO_ERRAND_TIME
    return choices.get("errand_time") or NO_ERRAND_TIME


def profile_from_choices(choices: dict | None) -> Profile:
    """選んだ内容から家の設定を作る。全部決まった語で、自由な文字は入らない。"""
    if choices is None:
        return Profile(name=OWN_HOME_NAME, configured=False)
    errand = choices.get("errand")
    people = (
        [ErrandPerson(label=errand, availability=availability_text(choices))]
        if errand and errand != "いない"
        else []
    )
    if any(key in choices for key in CHILD_KEYS):
        older_child = child_text(choices)
    else:
        # 前の画面の問い（家にいるきょうだいの通園・通学先）の答えだけがある古い台帳。
        school = choices.get("school")
        older_child = f"上の子は{school}" if school and school != "なし" else ""
    return Profile(
        name=OWN_HOME_NAME,
        bedside_others=["ほかに泊まれる人がいる"] if choices.get("bedside") == "いる" else [],
        errand_people=people,
        older_child=older_child,
        work=f"親は{choices['work']}" if choices.get("work") else "",
        supports=[
            Support(name=name, detail=SUPPORT_DETAILS.get(name, ""))
            for name in choices.get("supports", [])
        ],
        # 頼める人を選んでいなければ未設定。空を「いない」と読まない。
        configured=errand is not None,
        choices=choices,
        contacts=contacts_from_choices(choices.get("contacts")),
    )


def new_own_family(choices: dict | None) -> Family:
    """自分の家庭。項目は空で始める。"""
    return Family(code=new_family_code(), profile=profile_from_choices(choices))


# 試す家庭の設定を直すときの、最初に選んである内容（画面の Menu.tsx の TRIAL_CHOICES と同じ）。
# プリセットのファイル（AIへの入力）は変えないので、変えた欄だけを置き換えるための比べる元。
TRIAL_CHOICES: dict[str, dict] = {
    # 家庭A：上の子は保育園（年齢などはプリセットに無いので選ばない）、夫は夜と朝に動ける。
    "家庭A": {
        "parent": "母",
        "work": "会社員",
        "errand": "夫・パートナー",
        "bedside": "いない",
        "supports": ["延長保育"],
        "no_support": False,
        "contacts": None,
        "child_age": None,
        "child_sex": None,
        "child_school": None,
        "siblings": [{"age": None, "sex": None, "school": "保育園・幼稚園"}],
        "errand_times": ["平日の朝", "平日の夜"],
    },
    # 家庭B：上の子・支援なし、祖父母は平日の昼も夜も動ける。
    "家庭B": {
        "parent": "母",
        "work": "会社員",
        "errand": "祖父母",
        "bedside": "いない",
        "supports": [],
        "no_support": True,
        "contacts": ["夫", "職場"],
        "child_age": None,
        "child_sex": None,
        "child_school": None,
        "siblings": [],
        "errand_times": ["平日の日中", "平日の夜"],
    },
}

# 選ぶ欄と、それで決まる profile の欄。変えた組だけを置き換える。
_CHOICE_GROUPS: dict[tuple[str, ...], tuple[str, ...]] = {
    ("errand", "errand_time", "errand_times"): ("errand_people",),
    ("bedside",): ("bedside_others",),
    ("school", "child_age", "child_sex", "child_school", "siblings"): ("older_child",),
    ("work",): ("work",),
    ("supports", "no_support"): ("supports",),
    ("contacts",): ("contacts",),
}
_ALL_CONTACTS = {"夫", "保育園", "職場"}


def _same_choice(key: str, before: object, after: object) -> bool:
    if key == "contacts":
        # 3つとも選んだのは、選んでいない（誰も外さない）のと同じ。
        norm = lambda value: None if value is None or set(value) == _ALL_CONTACTS else set(value)
        return norm(before) == norm(after)
    if key in ("supports", "errand_times"):
        return set(before or []) == set(after or [])
    if key == "siblings":
        # 送られなかった欄と空（None）は同じ。
        norm = lambda value: [
            {field: (entry or {}).get(field) for field in ("age", "sex", "school")}
            for entry in value or []
        ]
        return norm(before) == norm(after)
    return before == after


def trial_profile_from_choices(profile: Profile, choices: dict) -> Profile:
    """試す家庭の設定を直す。変えた欄の組だけを選んだ語で置き換え、ほかはプリセットの事情の文を残す。

    何も変えていなければ profile に触らない（choices も書かない。AIへの入力を変えない）。
    QA Test の P1：全部を選んだ語で置き換えると、動ける時間や祖父母の事情の文が消えるため。
    """
    base = profile.choices or TRIAL_CHOICES.get(profile.name) or {}
    fresh = profile_from_choices(choices)
    update: dict = {}
    for keys, fields in _CHOICE_GROUPS.items():
        if all(_same_choice(key, base.get(key), choices.get(key)) for key in keys):
            continue
        for field in fields:
            update[field] = getattr(fresh, field)
    # 事情の文に効かない答え（あなたは 母／父 など）だけを変えたときも、選んだ内容は残す（QA Test の P2）。
    differs = any(
        not _same_choice(key, base.get(key), choices.get(key)) for key in set(base) | set(choices)
    )
    if not update and not differs and profile.choices is None:
        return profile
    return profile.model_copy(update={**update, "choices": choices, "configured": True})
