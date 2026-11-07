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


def profile_from_choices(choices: dict | None) -> Profile:
    """選んだ内容から家の設定を作る。全部決まった語で、自由な文字は入らない。"""
    if choices is None:
        return Profile(name=OWN_HOME_NAME, configured=False)
    errand = choices.get("errand")
    people = (
        [
            ErrandPerson(
                label=errand, availability=choices.get("errand_time") or "動ける時間は未設定"
            )
        ]
        if errand and errand != "いない"
        else []
    )
    school = choices.get("school")
    return Profile(
        name=OWN_HOME_NAME,
        bedside_others=["ほかに泊まれる人がいる"] if choices.get("bedside") == "いる" else [],
        errand_people=people,
        older_child=f"上の子は{school}" if school and school != "なし" else "",
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
    "家庭A": {
        "parent": "母",
        "school": "保育園",
        "work": "会社員",
        "errand": "夫・パートナー",
        "errand_time": "平日の夜と朝",
        "bedside": "いない",
        "supports": ["延長保育"],
        "no_support": False,
        "contacts": None,
    },
    "家庭B": {
        "parent": "母",
        "school": "なし",
        "work": "会社員",
        "errand": "祖父母",
        "errand_time": "日中も動ける",
        "bedside": "いない",
        "supports": [],
        "no_support": True,
        "contacts": ["夫", "職場"],
    },
}

# 選ぶ欄と、それで決まる profile の欄。変えた組だけを置き換える。
_CHOICE_GROUPS: dict[tuple[str, ...], tuple[str, ...]] = {
    ("errand", "errand_time"): ("errand_people",),
    ("bedside",): ("bedside_others",),
    ("school",): ("older_child",),
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
    if key == "supports":
        return set(before or []) == set(after or [])
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
