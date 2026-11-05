"""伝え直しの案の中身のeval。関門1の4件を本物のAPIに当て、台本に要る案が出るかを数える。

Claude APIを呼ぶので費用がかかる（1件につき最大2回×回数）。
使い方: python -X utf8 scripts/eval_retell.py --run [--reps 2] [--only A1,B1]
結果は evals/retell/results/ に書く（合成データだけなので公開してよい）。
"""

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import anthropic

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from ensemble.budget import JST, DailyLimitReached, consume_call  # noqa: E402
from ensemble.config import ROOT  # noqa: E402
from ensemble.db import init_db, make_engine  # noqa: E402
from ensemble.ledger import Action, ActionType, Certainty, Branch, Part  # noqa: E402
from ensemble.presets import load_family_from_preset  # noqa: E402
from ensemble.retell import MissingKey, api_key, cost_usd, make_client, retell  # noqa: E402

WORK_FORBIDDEN = re.compile(
    r"有給|年休|休暇|休業|休職|介護|看護|傷病|手当|肺炎|喘息|ウイルス|感染"
    r"|復帰します|出社します|戻ります|に退院します|退院が決まり"
)
CASES_PATH = ROOT / "evals" / "gate1" / "cases.json"
EXPECT_PATH = ROOT / "evals" / "retell" / "expect.json"
RESULTS_DIR = ROOT / "evals" / "retell" / "results"


def action_matches(action: Action, wanted: dict[str, Any]) -> bool:
    if action.part != wanted["part"]:
        return False
    if "type" in wanted and action.type != wanted["type"]:
        return False
    if "release" in wanted and wanted["release"] not in action.release:
        return False
    return "draft_has" not in wanted or wanted["draft_has"] in (action.draft or "")


def problems(branch: Branch, expect: dict[str, Any]) -> list[str]:
    actions = [
        action
        for outcome in branch.outcomes
        for action in outcome.actions
        if action.type != ActionType.NOTHING
    ]
    found = [
        f"必要な案がありません: {wanted}"
        for wanted in expect.get("must", [])
        if not any(action_matches(action, wanted) for action in actions)
    ]
    found += [
        f"出してはいけない案があります: {wanted}"
        for wanted in expect.get("must_not", [])
        if any(action_matches(action, wanted) for action in actions)
    ]
    if "only_types" in expect:
        found += [
            f"使えない種類です: {action.part}・{action.type}"
            for action in actions
            if action.type not in expect["only_types"]
        ]
    # 職場への文の決まり。制度の名前と病名を書かず、確定した日のように書かない。
    for action in actions:
        if action.part == Part.WORK and action.draft:
            hits = WORK_FORBIDDEN.findall(action.draft)
            if hits:
                found.append(f"職場への文に書かない言葉があります: {hits}")
    # 再検査の日は退院の候補日ではない。
    for day in expect.get("not_candidate_days", []):
        if any(day in candidate.day for candidate in branch.candidate_days):
            found.append(f"{day}が候補日に入っています")
    # 検査の日は、次の日の種類でも検査として出る（Plan review P1-C）。
    for name, kind in expect.get("next_kinds", {}).items():
        for outcome in branch.outcomes:
            for step in outcome.next:
                if name in step.day and step.kind.value != kind:
                    found.append(f"{name}の種類が{step.kind.value}です")
    unsure = expect.get("not_confirmed_day")
    if unsure and any(
        unsure in d.day and d.certainty == Certainty.CONFIRMED for d in branch.candidate_days
    ):
        found.append(f"{unsure}を確定にしています")
    wanted_day = expect.get("candidate_day")
    if wanted_day and not any(wanted_day in d.day for d in branch.candidate_days):
        found.append(f"{wanted_day}が候補日にありません")
    # 「よければ」の道では、候補日を取り消さない。
    kept = expect.get("not_cancelled_day")
    if kept and any(d.day == kept and d.cancelled for d in branch.candidate_days):
        found.append(f"{kept}を取り消しています")
    day = expect.get("cancelled_day")
    if day and not any(d.day == day and d.cancelled for d in branch.candidate_days):
        found.append(f"{day}が取り消しで残っていません")
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Claude APIを実際に呼ぶ")
    parser.add_argument("--reps", type=int, default=2)
    parser.add_argument("--cases", default="", help="件の一覧のファイル（省略時は関門1の4件）")
    parser.add_argument("--only", default="", help="件のIDをコンマで区切って絞る（例 A1,B1）")
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
    cases_path = Path(args.cases) if args.cases else CASES_PATH
    cases = json.loads(cases_path.read_text(encoding="utf-8"))["cases"]
    if args.only:
        cases = [case for case in cases if case["id"] in args.only.split(",")]
    expects = json.loads(EXPECT_PATH.read_text(encoding="utf-8"))["cases"]
    sections: list[str] = []
    passed = 0
    runs = 0
    not_run = 0
    total_cost = 0.0
    for rep in range(1, args.reps + 1):
        for case in cases:
            family = load_family_from_preset(case["home"], extra_items=case["extra_items"])
            print(f"{case['id']}（{rep}回目）を実行中…", flush=True)
            runs += 1
            title = f"## {case['id']}（{rep}回目）"
            try:
                result = retell(
                    client,
                    family,
                    case["trigger_item_id"],
                    case["stage"],
                    before_call=lambda: consume_call(engine),
                    chosen=case.get("chosen"),
                )
            except DailyLimitReached:
                sections.append(f"{title}\n- 今日の回数の上限に届いたので止めました")
                not_run += 1
                continue
            except anthropic.APIError as error:
                sections.append(f"{title}\n- APIのエラー：{type(error).__name__}: {error}")
                not_run += 1
                continue
            cost = sum(cost_usd(attempt.usage) for attempt in result.attempts)
            total_cost += cost
            found = (
                problems(result.branch, expects[case["id"]])
                if result.branch
                else ["案が出ませんでした（守りで2回落ちた）"]
            )
            passed += not found
            lines = [
                f"{title}：{'合格' if not found else '不合格'}",
                f"- 呼び出し回数：{len(result.attempts)}／費用の見積もり：${cost:.3f}",
            ]
            lines += [f"- 問題：{problem}" for problem in found]
            for number, attempt in enumerate(result.attempts, 1):
                lines += [f"- {number}回目の守りの指摘：{v}" for v in attempt.violations]
            if result.branch:
                for outcome in result.branch.outcomes:
                    lines.append(f"\n**{outcome.label}**\n")
                    for action in outcome.actions:
                        release = f"／外す：{action.release}" if action.release else ""
                        lines.append(
                            f"- {action.part}・{action.type}"
                            f"（根拠：{', '.join(action.basis_ids)}{release}）\n"
                            f"  > {action.draft or '（文なし）'}"
                        )
            sections.append("\n".join(lines))

    now = datetime.now(JST)
    body = (
        f"# 伝え直しのeval {now:%Y-%m-%d %H:%M}\n\n"
        f"**{passed}/{runs} 回で合格**（呼べなかった回：{not_run}）\n"
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
