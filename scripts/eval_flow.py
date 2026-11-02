"""入れる→仕分け→案→押す→台帳に入る、をAPI経由で本物のClaudeに通す。合成データだけ。

Claude APIを呼ぶので費用がかかる（1家庭につき2回から4回）。DBは一時フォルダの SQLite。
使い方: python -X utf8 scripts/eval_flow.py --run
"""

import argparse
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from ensemble.db import init_db  # noqa: E402
from ensemble.main import create_app  # noqa: E402
from ensemble.retell import MissingKey, api_key  # noqa: E402

RESULT_TEXT = "今日の採血はだめでした"


def run_home(client: TestClient, home: str) -> bool:
    print(f"== {home} ==")
    made = client.post("/api/boards", json={"home": home}).json()
    headers = {"X-Family-Code": made["code"]}
    before = made["board"]["items"]
    sorted_res = client.post(
        "/api/board/intake", json={"request_id": "flow-1", "text": RESULT_TEXT}, headers=headers
    )
    print("仕分け", sorted_res.status_code)
    if sorted_res.status_code != 200:
        return False
    body = sorted_res.json()
    print("きっかけ", bool(body["trigger"]), "項目", len(body["added"]))
    if not body["trigger"]:
        return False
    proposal = client.post(
        "/api/board/proposal", json={"item_id": body["trigger"]}, headers=headers
    ).json()
    print("段階", proposal.get("stage"), "案", bool(proposal.get("branch")))
    if not proposal.get("branch"):
        return False
    unchanged = (
        client.get("/api/board", headers=headers).json()["version"] == body["board"]["version"]
    )
    print("案を作っても台帳は同じ", unchanged)
    accepted = client.post(
        "/api/board/branches",
        json={"version": body["board"]["version"], "branch": proposal["branch"]},
        headers=headers,
    )
    print("進める", accepted.status_code)
    if accepted.status_code != 201:
        return False
    saved = accepted.json()
    same_status = all(
        old["status"] == new["status"] for old, new in zip(before, saved["items"], strict=False)
    )
    print("項目の状態は同じ", same_status)
    for outcome in saved["branches"][0]["outcomes"]:
        for action in outcome["actions"]:
            print(" ", action["part"], action["type"], "外す" if action["release"] else "")
    return unchanged and same_status


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Claude APIを実際に呼ぶ")
    args = parser.parse_args()
    if not args.run:
        print("費用がかかるので --run を付けたときだけ動きます。")
        return 2
    try:
        print(f"キーの末尾4文字：…{api_key()[-4:]}")
    except MissingKey as error:
        print(f"止めました：{error}")
        return 3
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        engine = create_engine(f"sqlite:///{(Path(tmp) / 'flow.db').as_posix()}")
        init_db(engine)
        client = TestClient(create_app(engine, dist=Path(tmp)))
        results = {home: run_home(client, home) for home in ("home_a", "home_b")}
        engine.dispose()
    print(results)
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
