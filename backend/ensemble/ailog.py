"""Claudeの呼び出しの入力と出力を jsonl に残す（evalと判断の記録の材料）。

家族コードはボードの鍵なので、記録には伏せて書く。
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

from .config import AI_LOG_PATH

_log_lock = Lock()


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
    with _log_lock, path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def forget_family(code: str, path: Path = AI_LOG_PATH) -> None:
    """そのボードの呼び出しを記録から取り除く。記録には入れた文が入っているため。"""
    mask = mask_code(code)
    with _log_lock:
        if not path.exists():
            return
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        kept = [line for line in lines if json.loads(line).get("family") != mask]
        temporary = path.with_suffix(".tmp")
        temporary.write_text("".join(kept), encoding="utf-8")
        temporary.replace(path)
