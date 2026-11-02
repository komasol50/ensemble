"""関門1の試験台。手書きの台帳4件に伝え直しを当て、根拠IDが全部正しいかを数える。

Claude APIを呼ぶので費用がかかる（1件につき最大2回。呼び直しのとき）。
プロンプトの調整は繰り返さない。1回回して結果を見る。

使い方: python -X utf8 scripts/gate1.py --run
結果は evals/gate1/results/ に書く（合成データだけなので公開してよい）。
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import anthropic

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from ensemble.budget import JST, DailyLimitReached, consume_call  # noqa: E402
from ensemble.config import ROOT  # noqa: E402
from ensemble.db import init_db, make_engine  # noqa: E402
from ensemble.guards import resolve_basis  # noqa: E402
from ensemble.ledger import Action, Family  # noqa: E402
from ensemble.presets import load_family_from_preset  # noqa: E402
from ensemble.retell import (
    MissingKey,
    RetellResult,
    api_key,
    cost_usd,
    make_client,
    retell,
)  # noqa: E402

CASES_PATH = ROOT / "evals" / "gate1" / "cases.json"
RESULTS_DIR = ROOT / "evals" / "gate1" / "results"
GATE_PASS = 3


def basis_all_correct(branch: dict | None, family: Family) -> bool:
    """1回目の出力で、案の根拠IDと外す項目のIDが全部台帳にあるか（案が1つ以上あること）。"""
    if not branch:
        return False
    actions = [
        Action.model_validate(action)
        for outcome in branch["outcomes"]
        for action in outcome["actions"]
    ]
    if not actions:
        return False
    return all(not resolve_basis(action, family.items)[1] for action in actions)


def render_case(case: dict, family: Family, result: RetellResult) -> tuple[str, bool, float]:
    first = result.attempts[0].branch if result.attempts else None
    correct = basis_all_correct(first, family)
    cost = sum(cost_usd(attempt.usage) for attempt in result.attempts)
    lines = [
        f"## {case['id']}（{case['home']}・{case['stage']}）",
        f"- 見たいこと：{case['look_for']}",
        f"- 1回目の根拠ID：{'全部正しい' if correct else '誤りあり'}",
        f"- 呼び出し回数：{len(result.attempts)}／守りの最終結果："
        f"{'通過' if result.branch else result.message}",
        f"- 費用の見積もり：${cost:.3f}",
    ]
    for number, attempt in enumerate(result.attempts, 1):
        if attempt.violations:
            lines.append(f"- {number}回目の守りの指摘：")
            lines += [f"  - {violation}" for violation in attempt.violations]
    shown = result.branch.model_dump(mode="json") if result.branch else first
    if shown:
        days = "、".join(
            f"{d['day']}（{d['certainty']}{'・取り消し' if d['cancelled'] else ''}）"
            for d in shown["candidate_days"]
        )
        lines.append(f"- 候補日：{days or 'なし'}")
        for outcome in shown["outcomes"]:
            lines.append(f"\n**{outcome['label']}**\n")
            for action in outcome["actions"]:
                release = f"／外す：{action['release']}" if action["release"] else ""
                lines.append(
                    f"- {action['part']}・{action['type']}"
                    f"（根拠：{', '.join(action['basis_ids'])}{release}）\n"
                    f"  > {action['draft'] or '（文なし）'}"
                )
    return "\n".join(lines), correct, cost


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
    total_cost = 0.0
    for case in cases:
        family = load_family_from_preset(case["home"], extra_items=case["extra_items"])
        print(f"{case['id']} を実行中…", flush=True)
        try:
            result = retell(
                client,
                family,
                case["trigger_item_id"],
                case["stage"],
                before_call=lambda: consume_call(engine),
            )
        except DailyLimitReached:
            sections.append(f"## {case['id']}\n- 今日の回数の上限に届いたので止めました")
            break
        except anthropic.APIError as error:
            sections.append(f"## {case['id']}\n- APIのエラー：{type(error).__name__}: {error}")
            continue
        section, correct, cost = render_case(case, family, result)
        sections.append(section)
        passed += correct
        total_cost += cost

    now = datetime.now(JST)
    verdict = "合格" if passed >= GATE_PASS else "未達"
    body = (
        f"# 関門1の結果 {now:%Y-%m-%d %H:%M}\n\n"
        f"**{passed}/{len(cases)} 件で根拠IDが全部正しい（合格は{GATE_PASS}件以上）：{verdict}**\n"
        f"費用の見積もり：${total_cost:.3f}\n\n" + "\n\n".join(sections) + "\n"
    )
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / f"{now:%Y%m%d-%H%M}.md"
    out.write_text(body, encoding="utf-8", newline="\n")
    print(body)
    print(f"書き出し先：{out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
