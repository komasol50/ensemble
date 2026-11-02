"""取り込みの小さなeval。合成データだけを本物のAPIに当て、欄の合否を数える。

Claude APIを呼ぶので費用がかかる（1件につき最大2回）。
使い方: python -X utf8 scripts/eval_intake.py --run
結果は evals/intake/results/ に書く（合成データだけなので公開してよい）。
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import anthropic

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from ensemble.budget import JST, DailyLimitReached, consume_call  # noqa: E402
from ensemble.config import ROOT  # noqa: E402
from ensemble.db import init_db, make_engine  # noqa: E402
from ensemble.intake import find_trigger, intake  # noqa: E402
from ensemble.ledger import Item  # noqa: E402
from ensemble.presets import load_family_from_preset  # noqa: E402
from ensemble.retell import MissingKey, api_key, cost_usd, make_client  # noqa: E402

CASES_PATH = ROOT / "evals" / "intake" / "cases.json"
RESULTS_DIR = ROOT / "evals" / "intake" / "results"


def matches(item: Item, expected: dict[str, Any]) -> bool:
    for name, value in expected.items():
        if name == "has_condition":
            actual: Any = bool(item.condition)
        elif name == "has_original":
            actual = bool(item.text_original)
        elif name == "has_summary":
            actual = bool(item.task_summary)
        else:
            actual = getattr(item, name)
        if actual != value:
            return False
    return True


def problems(case: dict, items: list[Item], trigger: str | None) -> list[str]:
    found = [
        f"期待の項目がありません: {expected}"
        for expected in case["expect"]
        if not any(matches(item, expected) for item in items)
    ]
    found += [
        f"あってはいけない項目があります: {forbid}"
        for forbid in case.get("forbid", [])
        if any(matches(item, forbid) for item in items)
    ]
    if trigger != case["trigger"]:
        found.append(f"きっかけ: {trigger}（期待は {case['trigger']}）")
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Claude APIを実際に呼ぶ")
    args = parser.parse_args()
    if not args.run:
        print("費用がかかるので --run を付けたときだけ動きます。")
        return 2
    try:
        print(f"キーの末尾4文字：…{api_key()[-4:]}")
        client = make_client()
    except MissingKey as error:
        print(f"止めました：{error}")
        return 3

    engine = make_engine()
    init_db(engine)
    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))["cases"]
    sections: list[str] = []
    passed = 0
    not_run = 0
    total_cost = 0.0
    for case in cases:
        family = load_family_from_preset(case["home"])
        print(f"{case['id']} を実行中…", flush=True)
        numbers = iter(range(100))
        try:
            result = intake(
                client,
                family,
                case["text"],
                datetime.now(JST).isoformat(timespec="seconds"),
                lambda: f"n-{next(numbers)}",
                before_call=lambda: consume_call(engine),
            )
        except DailyLimitReached:
            sections.append(f"## {case['id']}\n- 今日の回数の上限に届いたので止めました")
            not_run += 1
            break
        except anthropic.APIError as error:
            sections.append(f"## {case['id']}\n- APIのエラー：{type(error).__name__}: {error}")
            not_run += 1
            continue
        cost = sum(cost_usd(attempt.usage) for attempt in result.attempts)
        total_cost += cost
        trigger = find_trigger(result.items, [*family.items, *result.items])
        stage = trigger[1] if trigger else None
        found = problems(case, result.items, stage) if result.items else ["保存された項目なし"]
        passed += not found
        lines = [
            f"## {case['id']}：{'合格' if not found else '不合格'}",
            f"- 入れた文：{case['text']}",
            f"- 呼び出し回数：{len(result.attempts)}／費用の見積もり：${cost:.3f}",
        ]
        lines += [f"- 問題：{problem}" for problem in found]
        for number, attempt in enumerate(result.attempts, 1):
            lines += [f"- {number}回目の守りの指摘：{v}" for v in attempt.violations]
        for item in result.items:
            lines.append(
                f"- {item.kind}／{item.part}／{item.certainty}／医療 {item.medical}"
                f"／指す先 {item.refers_to}／発言者 {item.speaker}／条件 {item.condition}\n"
                f"  原文：{item.text_original}／用件：{item.task_summary}"
            )
        sections.append("\n".join(lines))

    now = datetime.now(JST)
    body = (
        f"# 取り込みのeval {now:%Y-%m-%d %H:%M}\n\n"
        f"**{passed}/{len(cases)} 件で合格**（呼べなかった件：{not_run}）\n"
        f"費用の見積もり：${total_cost:.3f}\n"
        "この試験の範囲での結果。\n\n" + "\n\n".join(sections) + "\n"
    )
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / f"{now:%Y%m%d-%H%M}.md"
    out.write_text(body, encoding="utf-8", newline="\n")
    print(body)
    print(f"書き出し先：{out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
