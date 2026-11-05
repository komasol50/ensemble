"""合成データ（プリセットと初期状態）を読み、台帳にする。"""

import json
from datetime import datetime, timedelta
from pathlib import Path

from .config import ROOT
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
        configured=True,
        choices=choices,
    )


def new_own_family(choices: dict | None) -> Family:
    """自分の家庭。項目は空で始める。"""
    return Family(code=new_family_code(), profile=profile_from_choices(choices))
