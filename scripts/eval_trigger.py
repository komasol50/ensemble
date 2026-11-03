"""結果の書き方を変えても「確かめる」が出るかを、本物のClaudeで測る。合成データだけ。

医師の言葉を入れてから結果を入れる、という実機と同じ順で API を通す。
Claude APIを呼ぶので費用がかかる（1回につき3呼び出し）。DBは一時フォルダの SQLite。
使い方: python -X utf8 scripts/eval_trigger.py --run [--reps 2] [--only 文の一部]
"""

import argparse
import json
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import create_engine

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from ensemble.config import AI_LOG_PATH  # noqa: E402
from ensemble.db import init_db  # noqa: E402
from ensemble.main import create_app  # noqa: E402
from ensemble.retell import PRICE_PER_MTOK, MissingKey, api_key  # noqa: E402

DOCTOR = "先生：明日の採血しだい。よければ木曜に帰れるかも。だめなら月曜にもう一回とって、それで大丈夫なら火曜かな"
RESULTS = [
    "今日の採血はだめでした",
    "採血はだめでした",
    "採血だめだった",
    "血液検査の結果がよくなかったです",
    "再検査になりました",
    "先生から採血は大丈夫と言われた",
]


def fields(board: dict[str, Any], item_id: str | None) -> dict[str, Any] | None:
    item = next((entry for entry in board["items"] if entry["id"] == item_id), None)
    if item is None:
        return None
    return {
        key: item[key] for key in ("kind", "part", "certainty", "refers_to", "condition", "medical")
    }


def run_one(client: TestClient, text: str, rep: int) -> dict[str, Any]:
    made = client.post("/api/boards", json={"home": "home_a"}).json()
    headers = {"X-Family-Code": made["code"]}
    first = client.post(
        "/api/board/intake", json={"request_id": "doctor", "text": DOCTOR}, headers=headers
    ).json()
    second_res = client.post(
        "/api/board/intake", json={"request_id": "result", "text": text}, headers=headers
    )
    row: dict[str, Any] = {
        "text": text,
        "rep": rep,
        "doctor_trigger": bool(first.get("trigger")),
        "intake_status": second_res.status_code,
    }
    if second_res.status_code != 200:
        return row
    second = second_res.json()
    row["added"] = [fields(second["board"], item_id) for item_id in second["added"]]
    row["trigger"] = bool(second["trigger"])
    if not second["trigger"]:
        return row
    proposal_res = client.post(
        "/api/board/proposal", json={"item_id": second["trigger"]}, headers=headers
    )
    proposal = proposal_res.json()
    row["proposal_status"] = proposal_res.status_code
    row["stage"] = proposal.get("stage")
    row["branch"] = bool(proposal.get("branch"))
    row["message"] = proposal.get("message")
    if proposal.get("branch"):
        row["actions"] = [
            f"{action['part']}・{action['type']}"
            for outcome in proposal["branch"]["outcomes"]
            for action in outcome["actions"]
        ]
    return row


def cost_since(start: int) -> float:
    if not AI_LOG_PATH.exists():
        return 0.0
    total = 0.0
    for line in AI_LOG_PATH.read_text(encoding="utf-8").splitlines()[start:]:
        usage = json.loads(line).get("usage") or {}
        total += usage.get("input_tokens", 0) * PRICE_PER_MTOK["input"] / 1e6
        total += usage.get("output_tokens", 0) * PRICE_PER_MTOK["output"] / 1e6
    return total


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--reps", type=int, default=2)
    parser.add_argument("--only", default="")
    args = parser.parse_args()
    texts = [text for text in RESULTS if args.only in text]
    if not args.run:
        print(f"{len(texts) * args.reps} 回。--run を付けると本物のAPIを呼びます。")
        return 0
    try:
        api_key()
    except MissingKey:
        print("キーがありません")
        return 2
    start = len(AI_LOG_PATH.read_text(encoding="utf-8").splitlines()) if AI_LOG_PATH.exists() else 0
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        engine = create_engine(f"sqlite:///{(Path(tmp) / 'trigger.db').as_posix()}")
        init_db(engine)
        client = TestClient(create_app(engine, dist=Path(tmp)))
        jobs = [(text, rep) for text in texts for rep in range(args.reps)]
        with ThreadPoolExecutor(max_workers=4) as pool:
            rows = list(pool.map(lambda job: run_one(client, *job), jobs))
        engine.dispose()

    lines = ["# 「確かめる」が出るか", "", f"測定 {datetime.now():%Y-%m-%d %H:%M}", ""]
    lines += [
        "| 入れた文 | きっかけ | 段階 | 案 | 新しい項目 | 案の中身・理由 |",
        "|---|---|---|---|---|---|",
    ]
    for row in rows:
        added = "; ".join(
            f"{a['kind']}/{a['part']}/{a['certainty']}/refers={a['refers_to']}/cond={bool(a['condition'])}"
            for a in row.get("added", [])
            if a
        )
        detail = "、".join(row.get("actions", [])) or (row.get("message") or "")
        lines.append(
            f"| {row['text']} | {row.get('trigger', row['intake_status'])} | {row.get('stage', '')} "
            f"| {row.get('branch', '')} | {added} | {detail} |"
        )
    triggered = sum(1 for row in rows if row.get("trigger"))
    proposed = sum(1 for row in rows if row.get("branch"))
    doctor = sum(1 for row in rows if row["doctor_trigger"])
    cost = cost_since(start)
    lines += [
        "",
        f"医師の言葉できっかけ：{doctor}/{len(rows)}",
        f"結果できっかけ：{triggered}/{len(rows)}",
        f"案まで出た：{proposed}/{len(rows)}",
        f"費用の見積もり：${cost:.2f}",
    ]
    report = "\n".join(lines)
    print(report)
    out = ROOT / "evals" / "trigger" / "results"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{datetime.now():%Y%m%d-%H%M}.md").write_text(report + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
