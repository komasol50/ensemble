"""合成データ（プリセットと初期状態）を読み、台帳にする。"""

import json
from pathlib import Path

from .config import ROOT
from .ledger import Family, Item, Profile, Rule, new_family_code

DATA_DIR = ROOT / "data"
HOMES = ("home_a", "home_b")


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


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
