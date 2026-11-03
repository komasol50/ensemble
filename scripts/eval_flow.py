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
DOCTOR_TEXT = (
    "先生：明日の採血しだい。よければ木曜に帰れるかも。"
    "だめなら月曜にもう一回とって、それで大丈夫なら火曜かな"
)
BOSS_TEXT = "上司：木曜の会議どうする？"
HUSBAND_TEXT = "夫：何持っていけばいい？"


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
    impact = saved["impact"]
    print("地図の見出し", impact["headline"])
    for node in impact["nodes"]:
        print(" ", node["part"], "／".join(node["effects"]), "：", node["task"])
    week = saved["week"]
    print(
        "候補のマス", [(row["label"], row["cells"][0]) for row in week["rows"] if row["cells"][0]]
    )
    return unchanged and same_status


def paste(client: TestClient, headers: dict, text: str, request_id: str) -> dict:
    res = client.post(
        "/api/board/intake", json={"request_id": request_id, "text": text}, headers=headers
    )
    body = res.json()
    print(f"貼る：{text[:24]}…", res.status_code, "項目", len(body.get("added", [])), end=" ")
    print("きっかけ", bool(body.get("trigger")))
    return body


def propose_and_accept(client: TestClient, headers: dict, trigger: str) -> dict | None:
    proposal = client.post("/api/board/proposal", json={"item_id": trigger}, headers=headers).json()
    print("段階", proposal.get("stage"), "案", bool(proposal.get("branch")))
    if not proposal.get("branch"):
        print("  理由", proposal.get("message"))
        return None
    board = client.get("/api/board", headers=headers).json()
    accepted = client.post(
        "/api/board/branches",
        json={"version": board["version"], "branch": proposal["branch"]},
        headers=headers,
    )
    print("進める", accepted.status_code)
    return accepted.json() if accepted.status_code == 201 else None


def set_status(client: TestClient, headers: dict, item_id: str, status: str, **extra: str) -> dict:
    board = client.get("/api/board", headers=headers).json()
    return client.patch(
        f"/api/board/items/{item_id}",
        json={"version": board["version"], "status": status, **extra},
        headers=headers,
    ).json()


def run_shoot(client: TestClient) -> bool:
    """撮影の順（台本の 0:33 → 2:02）に、撮影用の初期状態から本物のAIで通す。"""
    print("== 撮影の順 ==")
    made = client.post("/api/boards", json={"home": "shoot_a"}).json()
    headers = {"X-Family-Code": made["code"]}
    doctor = paste(client, headers, DOCTOR_TEXT, "shoot-1")
    paste(client, headers, BOSS_TEXT, "shoot-2")
    paste(client, headers, HUSBAND_TEXT, "shoot-3")
    if not doctor.get("trigger"):
        return False
    saved = propose_and_accept(client, headers, doctor["trigger"])
    if saved is None:
        return False
    days = [(row["label"], row["cells"][0]) for row in saved["week"]["rows"] if row["cells"][0]]
    print("条件の後の退院の列", days)
    childcare = [
        item
        for item in saved["items"]
        if item["part"] == "保育園・預け先" and item["status"] == "頼みたい"
    ]
    print("保育園の未依頼", [item["text_original"] for item in childcare])
    for item in childcare:
        set_status(client, headers, item["id"], "頼んだ")
    result = paste(client, headers, RESULT_TEXT, "shoot-4")
    if not result.get("trigger"):
        return False
    final = propose_and_accept(client, headers, result["trigger"])
    if final is None:
        return False
    impact = final["impact"]
    print("地図の見出し", impact["headline"])
    for node in impact["nodes"]:
        print(" ", node["part"], "／".join(node["effects"]), "：", node["task"])
        for entry in node["after"]:
            print("    ", entry["text"].replace("\n", " ")[:90])
    print(
        "候補のマス",
        [(row["label"], row["cells"][0]) for row in final["week"]["rows"] if row["cells"][0]],
    )
    latest = final["branches"][-1]["id"]
    family = [
        item
        for item in final["items"]
        if item["part"] == "家族" and item["id"].startswith(f"s-{latest}")
    ]
    print("夫への連絡", len(family), "ありか", [item["location"] for item in family])
    if not family:
        return False
    answered = client.post(
        f"/api/board/items/{family[0]['id']}/reply",
        json={"answer": "持っていく"},
        headers=headers,
    ).json()
    back = next(item for item in answered["items"] if item["id"] == family[0]["id"])
    print("夫の答え", back["status_label"], back["owner"])
    return back["status_label"] == "引き受けた" and any(
        node["part"] == "保育園・預け先" and "取り消し" in node["task"] for node in impact["nodes"]
    )


def run_home_b_scene(client: TestClient) -> bool:
    """家庭Bの場面：保育園が18:30までと答えても、その後の迎えは返答待ちで残る。"""
    print("== 家庭Bの場面 ==")
    made = client.post("/api/boards", json={"home": "home_b"}).json()
    headers = {"X-Family-Code": made["code"]}
    body = paste(client, headers, RESULT_TEXT, "scene-1")
    if not body.get("trigger"):
        return False
    saved = propose_and_accept(client, headers, body["trigger"])
    if saved is None:
        return False
    latest = saved["branches"][-1]["id"]
    made_items = [item for item in saved["items"] if item["id"].startswith(f"s-{latest}")]
    for item in made_items:
        print(
            " ", item["part"], item["status_label"], item["text_original"].replace("\n", " ")[:80]
        )
    care = next((item for item in made_items if item["part"] == "保育園・預け先"), None)
    support = next((item for item in made_items if item["part"] == "支援"), None)
    if care is None or support is None:
        print("保育園か支援の案がありません")
        return False
    set_status(client, headers, care["id"], "頼んだ")
    set_status(client, headers, support["id"], "頼んだ")
    after = set_status(client, headers, care["id"], "時刻つきで可", due="18:30")
    care_now = next(item for item in after["items"] if item["id"] == care["id"])
    support_now = next(item for item in after["items"] if item["id"] == support["id"])
    print("保育園", care_now["status_label"], "／支援", support_now["status_label"])
    family_requests = [item for item in made_items if item["part"] == "家族"]
    print("夫への連絡", [item["text_original"].replace("\n", " ")[:60] for item in family_requests])
    return (
        support_now["status_label"] == "返答待ち"
        and "18:30" in support["text_original"]
        and "◯" not in care_now["status_label"]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Claude APIを実際に呼ぶ")
    parser.add_argument("--shoot-only", type=int, default=0, help="撮影の順だけをこの回数通す")
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
        if args.shoot_only:
            results = {f"撮影の順{n + 1}": run_shoot(client) for n in range(args.shoot_only)}
        else:
            results = {home: run_home(client, home) for home in ("home_a", "home_b")}
            results["撮影の順"] = run_shoot(client)
            results["家庭Bの場面"] = run_home_b_scene(client)
        engine.dispose()
    print(results)
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
