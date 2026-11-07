"""入院初日の職場への文（v3.5.11）の eval。公開の関門（Plan main v3.5.11 の条件5）。

合成の4件を、それぞれ2回ずつ作る（4件×2回）：
- W1「今日から入院。いつまでかは分からない」型（会議の名指しなし）
- W2 会議の名指しあり
- W3 病名を含む入力（会議の名指しなし）
- W4 日付を含む入力（医師の言葉の候補日あり、打ち合わせの名指しあり）

合格：全8回で、文が出て、4つの決まり（T-H）を満たし、病名0・作った日付0・医療の語0。
4つの決まりの見方（台本の側で、文の語を数える）：
1. いつまで：候補日が無いときは「まだ分かりません／決まりしだい」などを書く。候補日があるときは
   見込みの語（見込み・かも・まだ決まって など）と一緒に書く。
2. 会議：名指しがある入力では、その会議を名指しし「代役」か「延期」を書く。無い入力では会議や
   打ち合わせを書かない（原文に無い会議を作らない）。
3. 事情：「付き添」まで。病名や医療の事情の語を書かない。
4. 制度：休みの制度の名前を書かず、「扱い」と「相談」を書く。
守りで落ちて呼び直した回数も書く（呼び直しの後に出た文で合否を見る）。

Claude APIを呼ぶので費用がかかる（1回につき最大2回の呼び出し。8回で最大16回）。
DBは一時フォルダの SQLite。回ごとにボードを作る。
使い方:
  python -X utf8 scripts/eval_work_first.py --run    # 本物のClaudeで測る。結果は evals/work_first/results/
  python -X utf8 scripts/eval_work_first.py --fake   # 偽のAIで、台本が最後まで動くかだけ見る（費用なし・書き出さない）
"""

import argparse
import os
import subprocess
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import create_engine

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))

import ensemble.work_first  # noqa: E402
from ensemble.db import init_db  # noqa: E402
from ensemble.guards import (  # noqa: E402
    check_words,
    extract_date_tokens,
    find_medical_words,
    find_symptom_words,
)
from ensemble.main import create_app  # noqa: E402
from ensemble.retell import PRICE_PER_MTOK, MissingKey, api_key  # noqa: E402
from ensemble.work_first import LEAVE_SYSTEM_PATTERN, find_illness_words  # noqa: E402

RESULTS = ROOT / "evals" / "work_first" / "results"
RUNS = 2

OWN_CHOICES = {
    "parent": "母",
    "school": "保育園",
    "work": "会社員",
    "errand": "夫・パートナー",
    "errand_time": "平日の夜と朝",
    "bedside": "いない",
    "supports": ["延長保育"],
    "no_support": False,
}


def work(text: str) -> dict[str, Any]:
    """親が入れた職場あての頼みごと。"""
    return {
        "kind": "行動",
        "part": "職場",
        "certainty": "見込み",
        "text_original": text,
        "task_summary": "休みの連絡",
        "speaker": "親",
        "status": "頼みたい",
    }


DOCTOR_W4 = {
    "kind": "情報",
    "part": "病院",
    "certainty": "見込み",
    "text_original": "先生：金曜の結果しだい。よければ来週の月曜に退院できるかも",
    "task_summary": "結果しだいで退院の候補が決まる",
    "speaker": "医師",
    "condition": "金曜の結果",
    "medical": "info",
}

# 項目の先頭が対象（職場あての頼みごと）。meeting は名指しされるはずの語、forbidden は出てはいけない語。
CASES: list[dict[str, Any]] = [
    {
        "id": "W1",
        "label": "いつまでか分からない型",
        "items": [work("今日から子どもが入院した。いつまでかは分からない。会社に休みの連絡をする")],
        "meeting": None,
        "forbidden": [],
        "candidate": False,
    },
    {
        "id": "W2",
        "label": "会議の名指しあり",
        "items": [
            work(
                "部長に休みの連絡をする。今日から子どもが入院。"
                "木曜10時の定例会議には出られない。資料は共有フォルダにある"
            )
        ],
        "meeting": "定例会議",
        "forbidden": [],
        "candidate": False,
    },
    {
        "id": "W3",
        "label": "病名を含む入力",
        "items": [work("子どもがRSウイルスで入院、肺炎の疑いと言われた。上司に休みの連絡をする")],
        "meeting": None,
        "forbidden": ["RSウイルス", "RS", "ウイルス", "肺炎"],
        "candidate": False,
    },
    {
        "id": "W4",
        "label": "日付を含む入力（医師の言葉の候補日あり）",
        "items": [
            work(
                "11月10日（火）の15時の打ち合わせは出られないと上司に伝える。明日の朝までに連絡したい"
            ),
            DOCTOR_W4,
        ],
        "meeting": "打ち合わせ",
        "forbidden": ["採血", "検査"],
        "candidate": True,
    },
]

# 代役・延期の言い換え（決まり2）。
DELEGATE_WORDS = (
    "代役",
    "延期",
    "ほかの方",
    "他の方",
    "どなたか",
    "日を改め",
    "日程を変え",
    "別の日",
)
UNDECIDED = (
    "まだ分かりません",
    "まだわかりません",
    "決まりしだい",
    "決まり次第",
    "未定",
    "まだ決まって",
)
HEDGE = UNDECIDED + ("見込み", "かも", "候補")
MEETING_WORDS = ("会議", "定例", "打ち合わせ", "打合せ", "ミーティング", "MTG", "商談", "面談")

_lock = threading.Lock()
_records: list[dict[str, Any]] = []


def install_capture() -> None:
    """AIの呼び出しの記録を、台本の側でも受け取る（守りで落ちた数と費用を数えるため）。

    偽のAIは記録を止めているので、偽のAIを読み込んだ後に差し込む。
    """
    original = ensemble.work_first.record_ai_call

    def capture(name: str, inputs: Any, outputs: Any, **kw: Any) -> Any:
        with _lock:
            _records.append(
                {
                    "family": kw.get("family_code"),
                    "outputs": outputs,
                    "usage": kw.get("usage") or {},
                }
            )
        return original(name, inputs, outputs, **kw)

    ensemble.work_first.record_ai_call = capture


def commit_id() -> str:
    try:
        head = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--", "backend"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        ).stdout.strip()
        return f"{head}{'（backend に未コミットの変更あり）' if dirty else ''}"
    except OSError:
        return "不明"


def make_once(client: TestClient, case: dict[str, Any]) -> dict[str, Any]:
    """ボードを作り、合成の項目を入れて、職場への文を1回作る。"""
    code = client.post("/api/boards", json={"home": "own", "choices": OWN_CHOICES}).json()["code"]
    headers = {"X-Family-Code": code}
    ids: list[str] = []
    for values in case["items"]:
        version = client.get("/api/board", headers=headers).json()["version"]
        res = client.post("/api/board/items", json={"version": version, **values}, headers=headers)
        res.raise_for_status()
        ids.append(res.json()["items"][-1]["id"])
    res = client.post(f"/api/board/items/{ids[0]}/work-first", headers=headers)
    body = res.json() if res.status_code == 200 else {}
    return {"status": res.status_code, "body": body, "family": code}


def judge(case: dict[str, Any], draft: str | None) -> list[str]:
    """落ちた決まりの一覧（空なら合格）。"""
    if not draft:
        return ["文が出なかった"]
    misses: list[str] = []
    allowed: set[str] = set()
    for values in case["items"]:
        allowed |= extract_date_tokens(values.get("text_original") or "")
    invented = extract_date_tokens(draft) - allowed
    if invented:
        misses.append(f"作った日付 {sorted(invented)}")
    medical = find_medical_words(draft) + find_symptom_words(draft)
    if medical:
        misses.append(f"医療の語 {medical}")
    illness = find_illness_words(draft) + [word for word in case["forbidden"] if word in draft]
    if illness:
        misses.append(f"病名・医療の事情 {sorted(set(illness))}")
    # 1. いつまで
    if case["candidate"]:
        if not any(word in draft for word in HEDGE):
            misses.append("決まり1：候補日を見込みとして書いていない")
    elif not any(word in draft for word in UNDECIDED):
        misses.append("決まり1：「まだ分かりません／決まりしだい」が無い")
    # 2. 会議
    if case["meeting"]:
        if case["meeting"] not in draft:
            misses.append(f"決まり2：「{case['meeting']}」を名指ししていない")
        # 代役か延期かを相手に委ねる言い方。2026-11-07 14:50 の測定で「どなたかにお願いするか日を改めるか、
        # ご判断をお任せします」を語の照合だけで落とした（判定の誤り）ので、言い換えも数える。
        # 結果を見てから広げたので、その回は合格に数えず、広げた判定で測り直す（decisions.md）。
        # 委ねる語は、代役・延期の語と同じ文（。で区切った中）に求める。「ご相談」は決まり4の
        # 「扱いはご相談させてください」でいつも入るので数えない（dev-review の P2-1）。
        sentences = [part for part in draft.replace("\n", "。").split("。") if part]
        delegate = any(
            any(word in part for word in DELEGATE_WORDS)
            and any(word in part for word in ("お任せ", "ご判断", "お願いでき", "ご指示"))
            for part in sentences
        )
        if not delegate:
            misses.append("決まり2：代役か延期かを相手に委ねていない")
    else:
        made = [word for word in MEETING_WORDS if word in draft]
        if made:
            misses.append(f"決まり2：原文に無い会議・予定 {made}")
    # 3. 事情
    if "付き添" not in draft:
        misses.append("決まり3：「子の入院に付き添う」が無い")
    # 4. 制度
    leave = LEAVE_SYSTEM_PATTERN.findall(draft)
    if leave:
        misses.append(f"決まり4：制度の名前 {leave}")
    if "扱い" not in draft or "相談" not in draft:
        misses.append("決まり4：「扱いはご相談させてください」が無い")
    words = check_words(draft)
    if words:
        misses.append(words[0])
    return misses


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="本物のClaudeで測る（費用がかかる）")
    parser.add_argument("--fake", action="store_true", help="偽のAIで台本が動くかだけ見る")
    args = parser.parse_args()
    if args.run == args.fake:
        print("--run（本物のClaude、費用がかかる）か --fake（偽のAI）のどちらかを付けます。")
        return 2
    if args.run:
        try:
            api_key()
        except MissingKey:
            print("キーがありません")
            return 2
        client_factory: Any = None
    else:
        import fake_ai_app  # noqa: PLC0415  偽のAI。読み込むと記録を止める

        client_factory = fake_ai_app.FakeAI
    install_capture()
    # 関門の測定なので、公開の切り替えによらず、この台本の中だけ on にして測る。
    os.environ["WORK_FIRST"] = "1"
    os.environ["BOARD_CREATE_LIMIT"] = "100000"

    runs = [(case, n) for case in CASES for n in range(1, RUNS + 1)]
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        engine = create_engine(f"sqlite:///{(Path(tmp) / 'work.db').as_posix()}")
        init_db(engine)
        options: dict[str, Any] = {"dist": Path(tmp)}
        if client_factory is not None:
            options["client_factory"] = client_factory
        client = TestClient(create_app(engine, **options))
        with ThreadPoolExecutor(max_workers=4) as pool:
            done = list(pool.map(lambda run: make_once(client, run[0]), runs))
        engine.dispose()

    by_family: dict[str, list[dict[str, Any]]] = {}
    for record in _records:
        by_family.setdefault(record["family"], []).append(record)

    lines: list[str] = []
    passed_runs = 0
    retried = 0
    totals = {"作った日付": 0, "医療の語": 0, "病名・医療の事情": 0}
    for (case, n), result in zip(runs, done):
        draft = result["body"].get("draft")
        misses = judge(case, draft)
        if result["status"] != 200:
            misses.insert(0, f"応答 {result['status']}")
        calls = by_family.get(result["family"], [])
        dropped = [
            record["outputs"]["violations"] for record in calls if record["outputs"]["violations"]
        ]
        retried += len(calls) > 1
        for key in totals:
            totals[key] += any(miss.startswith(key) for miss in misses)
        passed_runs += not misses
        mark = "合格" if not misses else "不合格：" + "／".join(misses)
        lines.append(f"### {case['id']}-{n} {case['label']}：{mark}")
        lines.append(f"- 入力：{case['items'][0]['text_original']}")
        if len(case["items"]) > 1:
            lines.append(f"- 医師の言葉：{case['items'][1]['text_original']}")
        lines.append(f"- 呼び出し {len(calls)} 回")
        for violations in dropped:
            lines.append(f"  - 守りで落ちた：{'／'.join(violations)}")
        lines.append("")
        lines.append("```")
        lines.append(draft or f"（文なし：{result['body'].get('message')}）")
        lines.append("```")
        lines.append("")

    usage = [record["usage"] for record in _records]
    cost = sum(
        u.get("input_tokens", 0) * PRICE_PER_MTOK["input"] / 1e6
        + u.get("output_tokens", 0) * PRICE_PER_MTOK["output"] / 1e6
        for u in usage
    )
    total = len(runs)
    passed = passed_runs == total
    head = [
        "# 入院初日の職場への文の eval" + ("（偽のAI）" if args.fake else ""),
        "",
        f"測定 {datetime.now():%Y-%m-%d %H:%M}（この試験の範囲での結果）／版 {commit_id()}／"
        f"effort {ensemble.work_first.WORK_FIRST_EFFORT}",
        "",
        f"**{'合格' if passed else '不合格'}**：{passed_runs}/{total} 回が合格"
        f"（病名 {totals['病名・医療の事情']}・作った日付 {totals['作った日付']}・"
        f"医療の語 {totals['医療の語']} 回）",
        f"守りで落ちて呼び直した回：{retried}/{total}",
        f"AIの呼び出し {len(_records)} 回／費用の見積もり：${cost:.2f}",
        "",
        "合格なら work_first.WORK_FIRST を True（または配信先の環境変数 WORK_FIRST=1）にして公開できる。"
        "不合格なら off のまま公開する（今の「やることの1行」のまま）。",
        "",
    ]
    report = "\n".join(head + lines) + "\n"
    print(report)
    if args.run:
        RESULTS.mkdir(parents=True, exist_ok=True)
        (RESULTS / f"{datetime.now():%Y%m%d-%H%M}.md").write_text(report, encoding="utf-8")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
