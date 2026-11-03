"""動画とREADMEに出す評価の数字を、結果と呼び出しの記録から数える。Claudeは呼ばない。

数えるのは、指定の時刻より後に今のコードで測った分だけ。
- 根拠IDの不一致と日付の作り込みは、伝え直しの本物の出力に今の守りを当てて数える。
  1回目の出力で守りに当たったものを「AIの出力」、呼び直しの後に画面へ出たものを「画面」とする。
- 医療の判断の漏れは evals/medical/results の最新のファイルから取る。

使い方: python -X utf8 scripts/eval_numbers.py 2026-11-03T09:40
結果は evals/numbers.json に書く。
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from ensemble.guards import check_branch, resolve_basis  # noqa: E402
from ensemble.ledger import Branch, Family, Item, Profile, Rule  # noqa: E402

LEDGER = re.compile(r"<ledger>\n(.*)\n</ledger>", re.S)
SECTION = re.compile(r"^## (.+?)：(\d+)/(\d+)$", re.M)


def family_from(content: str) -> Family | None:
    found = LEDGER.search(content)
    if not found:
        return None
    ledger = json.loads(found[1])
    return Family(
        code="x" * 32,
        version=1,
        profile=Profile.model_validate(ledger["profile"]),
        rules=[Rule.model_validate(rule) for rule in ledger["rules"]],
        items=[Item.model_validate(item) for item in ledger["items"]],
    )


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    since = sys.argv[1]
    branches = drafts = actions = 0
    bad_basis = invented = 0
    retried = 0
    for line in (ROOT / "logs" / "ai_calls.jsonl").read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        if record.get("call") != "retell" or record["at"] < since:
            continue
        data = record["output"].get("branch")
        family = family_from(record["input"]["content"])
        if not data or family is None:
            continue
        branch = Branch.model_validate(data)
        branches += 1
        retried += bool(record["output"].get("violations"))
        for outcome in branch.outcomes:
            for action in outcome.actions:
                actions += 1
                drafts += bool(action.draft)
                _, problems = resolve_basis(action, family.items)
                bad_basis += bool(problems)
        invented += sum("根拠の原文に無い日付" in v for v in check_branch(branch, family))

    latest = sorted((ROOT / "evals" / "medical" / "results").glob("*.md"))[-1]
    sections = {m[1]: (int(m[2]), int(m[3])) for m in SECTION.finditer(latest.read_text("utf-8"))}
    asked = sections["判断を求める問い（固定文が出る）"]
    urgent = sections["急な症状（固定文が出る）"]
    missed = (asked[1] - asked[0]) + (urgent[1] - urgent[0])
    missed_total = asked[1] + urgent[1]
    result = {
        "measured_on": latest.stem[:4] + "-" + latest.stem[4:6] + "-" + latest.stem[6:8],
        "slide": [
            {"label": "医療の判断の漏れ", "value": missed, "total": missed_total},
            {"label": "根拠IDの不一致", "value": bad_basis, "total": actions},
            {"label": "日付の作り込み", "value": invented, "total": drafts},
        ],
        "detail": {
            "案の分かれ道": branches,
            "1回目で守りに当たって呼び直した分かれ道": retried,
            "案": actions,
            "下書き": drafts,
            "医療の結果ファイル": latest.name,
            "医療の境目（止めない）": sections.get("境目の生活の項目（止めない）"),
        },
    }
    out = ROOT / "evals" / "numbers.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
