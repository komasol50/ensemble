"""Claudeの呼び出しの入力と出力を jsonl に残す（evalと判断の記録の材料）。

家族コードはボードの鍵なので、記録には伏せて書く。
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import AI_LOG_PATH


def mask_code(code: str | None) -> str | None:
    """家族コードを先頭4文字だけ残して伏せる。"""
    if code is None:
        return None
    return code[:4] + "…"


def record_ai_call(
    call: str,
    input_data: Any,
    output_data: Any,
    *,
    family_code: str | None = None,
    model: str | None = None,
    usage: dict | None = None,
    path: Path = AI_LOG_PATH,
) -> dict:
    entry = {
        "at": datetime.now(timezone.utc).isoformat(),
        "call": call,
        "family": mask_code(family_code),
        "model": model,
        "usage": usage,
        "input": input_data,
        "output": output_data,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry
