"""合成データ（プリセットと初期状態）を読み、台帳にする。"""

import json
from datetime import datetime, timedelta
from pathlib import Path

from .config import ROOT
from .ledger import Family, Item, Profile, Rule, new_family_code

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
