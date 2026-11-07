"""入れる→仕分け→案→押す→台帳に入る、をAPI経由で本物のClaudeに通す。合成データだけ。

Claude APIを呼ぶので費用がかかる（1家庭につき2回から4回）。DBは一時フォルダの SQLite。
使い方: python -X utf8 scripts/eval_flow.py --run
"""

import argparse
import sys
import tempfile
from datetime import date, datetime
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


OWN_CHOICES = {
    "頼める人がいる": {
        "parent": "母",
        "adult": "いる",
        "child": "いる",
        "school": "保育園",
        "work": "会社員",
        "errand": "夫・パートナー",
        "errand_time": "平日の夜と朝",
        "bedside": "いない",
        "supports": ["延長保育"],
        "no_support": False,
    },
    "頼める人がいない": {
        "parent": "母",
        "adult": "いない",
        "child": "いる",
        "school": "保育園",
        "work": "会社員",
        "errand": "いない",
        "errand_time": None,
        "bedside": "いない",
        "supports": ["延長保育", "ファミサポ"],
        "no_support": False,
    },
}


def run_own(client: TestClient, name: str) -> bool:
    """作った家庭で、取り込みから通す。画面と同じく、選んだことを渡す。"""
    print(f"== 作った家庭（{name}） ==")
    made = client.post("/api/boards", json={"home": "own", "choices": OWN_CHOICES[name]}).json()
    headers = {"X-Family-Code": made["code"]}
    doctor = client.post(
        "/api/board/intake",
        json={"request_id": "own-1", "text": DOCTOR_TEXT, "said": "言われた", "who": "病院"},
        headers=headers,
    ).json()
    print(
        "医師の言葉", "きっかけ", bool(doctor.get("trigger")), "項目", len(doctor.get("added", []))
    )
    if not doctor.get("trigger"):
        return False
    first = propose_and_accept(client, headers, doctor["trigger"])
    if first is None:
        return False
    result = client.post(
        "/api/board/intake",
        json={
            "request_id": "own-2",
            "text": "月曜にもう一回とると言われた",
            "said": "言われた",
            "who": "病院",
        },
        headers=headers,
    ).json()
    print("結果", "きっかけ", bool(result.get("trigger")), "項目", len(result.get("added", [])))
    if not result.get("trigger"):
        return False
    second = propose_and_accept(client, headers, result["trigger"])
    if second is None:
        return False
    board = client.get("/api/board", headers=headers).json()
    branch = board["branches"][-1]
    actions = [action for outcome in branch["outcomes"] for action in outcome["actions"]]
    parts = {action["part"] for action in actions}
    days = {day["day"]: day for day in branch["candidate_days"]}
    has_next = all(outcome["next"] for outcome in branch["outcomes"])
    print("相手", sorted(parts), "候補日", list(days), "次の段", has_next)
    words = "".join(action["draft"] or "" for action in actions)
    good = not any("月曜" in day for day in days) and any(
        day["cancelled"] for key, day in days.items() if "木" in key
    )
    if name == "頼める人がいない":
        good = good and "家族" not in parts and "夫" not in words
    else:
        good = good and "家族" in parts
    print("基準", good)
    return good


def set_status(client: TestClient, headers: dict, item_id: str, status: str, **extra: str) -> dict:
    board = client.get("/api/board", headers=headers).json()
    return client.patch(
        f"/api/board/items/{item_id}",
        json={"version": board["version"], "status": status, **extra},
        headers=headers,
    ).json()


FIGURE_INPUT = (
    "先生から「12日に採血。退院できるなら16日が候補、再検査なら17日」と言われた。"
    "着替えも頼みたい。場所は寝室の棚。"
)
DAY: dict[str, date | None] = {"today": None}


def _follow_day() -> None:
    """撮影の筋書きの「今日」（11/9 の入力、11/12 の結果）を、サーバーの中だけで動かす。"""
    import ensemble.api

    real_now, real_today = ensemble.api.now_jst, ensemble.api.today_jst

    def now_jst() -> str:
        day = DAY["today"]
        if day is None:
            return real_now()
        now = datetime.now(ensemble.api.JST)
        return now.replace(year=day.year, month=day.month, day=day.day).isoformat(
            timespec="seconds"
        )

    ensemble.api.now_jst = now_jst
    ensemble.api.today_jst = lambda: DAY["today"] or real_today()


def merge_by_part(actions: list[dict]) -> list[dict]:
    """画面と同じく、同じ相手の用件を1通にまとめる（Decide.tsx の mergeByPart）。"""
    rank = ["何もしない", "確認する", "空きを確認する", "伝える", "頼む"]
    merged: dict[str, dict] = {}
    for action in actions:
        first = merged.get(action["part"])
        if first is None:
            merged[action["part"]] = dict(action)
            continue
        if action.get("draft") and action["type"] != "何もしない":
            texts = [text for text in (first.get("draft"), action["draft"]) if text]
            first["draft"] = "\n".join(texts)
        if rank.index(action["type"]) > rank.index(first["type"]):
            first["type"] = action["type"]
        first["basis_ids"] = list(dict.fromkeys(first["basis_ids"] + action["basis_ids"]))
        first["release"] = list(dict.fromkeys(first["release"] + action["release"]))
    return list(merged.values())


def run_shoot(client: TestClient) -> bool:
    """撮影の順（図 新UI_2026-11-05 の筋書き）を、撮影用の初期状態から本物のAIで通す。

    11/9 に医師の言葉と着替えの頼みごとを入れ、11/12 に「再検査になった」を選ぶ。
    夫・職場・保育園への文が出て、夫には用件が2つ（着替え、16日の迎え）、16日が取り消し、
    次の段に17日の再検査が出て、まとめて進めたあと夫の「持っていく」が戻るかを見る。
    """
    print("== 撮影の順（図の筋書き）==")
    DAY["today"] = date(2026, 11, 9)
    made = client.post("/api/boards", json={"home": "shoot_a"}).json()
    headers = {"X-Family-Code": made["code"]}
    paste(client, headers, FIGURE_INPUT, "figure-1")
    board = client.get("/api/board", headers=headers).json()
    request = [
        item["id"]
        for item in board["items"]
        if item["part"] == "家族" and item["speaker"] == "親" and item["status"] == "頼みたい"
    ]
    print("夫への頼みごと（親）", request)
    DAY["today"] = date(2026, 11, 12)
    board = client.get("/api/board", headers=headers).json()
    doctor = board.get("doctor")
    if not doctor:
        print("  医師の言葉が無い")
        return False
    titles = [option.get("title") for option in doctor["options"]]
    print("⑤の見出し", doctor.get("after_title"), "選択肢", titles)
    retest = next((o for o in doctor["options"] if o.get("title") == "再検査になった"), None)
    if retest is None:
        return False
    added = client.post(
        "/api/board/items",
        json={
            "version": board["version"],
            "kind": "情報",
            "part": "病院",
            "certainty": retest["certainty"],
            "text_original": retest["result"],
            "speaker": "親",
            "medical": "info",
            "refers_to": doctor["item_id"],
        },
        headers=headers,
    ).json()
    result_id = added["items"][-1]["id"]
    proposal = client.post(
        "/api/board/proposal",
        json={"item_id": result_id, "chosen": retest["label"]},
        headers=headers,
    ).json()
    branch = proposal.get("branch")
    if not branch:
        print("  理由", proposal.get("message"))
        return False
    actions = [action for outcome in branch["outcomes"] for action in outcome["actions"]]
    for action in actions:
        print(" ", action["part"], action["type"], "：", (action.get("ask") or "")[:40])
    parts = {action["part"] for action in actions if action.get("draft")}
    family = [action for action in actions if action["part"] == "家族" and action.get("draft")]
    cancelled = [day["day"] for day in branch["candidate_days"] if day.get("cancelled")]
    steps = [(s["day"], s["kind"]) for o in branch["outcomes"] for s in o.get("next", [])]
    print("相手", sorted(parts), "夫の用件", len(family), "取り消し", cancelled, "次の段", steps)
    branch["outcomes"] = [
        {**outcome, "actions": merge_by_part(outcome["actions"])} for outcome in branch["outcomes"]
    ]
    board = client.get("/api/board", headers=headers).json()
    accepted = client.post(
        "/api/board/branches",
        json={"version": board["version"], "branch": branch},
        headers=headers,
    )
    print("進める", accepted.status_code)
    if accepted.status_code != 201:
        return False
    saved = accepted.json()
    carried = [
        item["id"] for item in saved["items"] if item["id"] in request and item.get("carried_into")
    ]
    print("親の頼みごとが案の文に引き継がれた", carried)
    latest = saved["branches"][-1]["id"]
    sent = [i for i in saved["items"] if i["part"] == "家族" and i["id"].startswith(f"s-{latest}")]
    if not sent:
        return False
    print("夫への文", sent[0]["text_original"].replace("\n", " ")[:120])
    answered = client.post(
        f"/api/board/items/{sent[0]['id']}/reply", json={"answer": "持っていく"}, headers=headers
    ).json()
    back = next(item for item in answered["items"] if item["id"] == sent[0]["id"])
    print("夫の答え", back["status_label"], back["owner"])
    return (
        {"家族", "職場", "保育園・預け先"} <= parts
        and len(family) >= 2
        and any("16" in day for day in cancelled)
        and any(kind == "検査" and "17" in day for day, kind in steps)
        and back["status_label"] == "引き受けた"
    )


def run_shoot_b(client: TestClient) -> bool:
    """家庭Bの撮影の順（図の筋書き）。11/12 に「再検査になった」を選ぶ。

    2026-11-07 本人の決定：夫に頼めず、用事を頼める人は祖父母だけ。16日の迎えの取り消しが
    祖父母（家族）への文になり、職場への文も出て、17日の再検査が出るかを見る。夫への依頼は出ない。
    """
    print("== 家庭Bの撮影の順（図の筋書き）==")
    DAY["today"] = date(2026, 11, 12)
    made = client.post("/api/boards", json={"home": "shoot_b"}).json()
    headers = {"X-Family-Code": made["code"]}
    board = made["board"]
    doctor = board.get("doctor")
    retest = next(
        (o for o in (doctor or {}).get("options", []) if o.get("title") == "再検査になった"), None
    )
    if retest is None:
        print("  ⑤の選択肢が無い")
        return False
    added = client.post(
        "/api/board/items",
        json={
            "version": board["version"],
            "kind": "情報",
            "part": "病院",
            "certainty": retest["certainty"],
            "text_original": retest["result"],
            "speaker": "親",
            "medical": "info",
            "refers_to": doctor["item_id"],
        },
        headers=headers,
    ).json()
    proposal = client.post(
        "/api/board/proposal",
        json={"item_id": added["items"][-1]["id"], "chosen": retest["label"]},
        headers=headers,
    ).json()
    branch = proposal.get("branch")
    if not branch:
        print("  理由", proposal.get("message"))
        return False
    actions = [action for outcome in branch["outcomes"] for action in outcome["actions"]]
    for action in actions:
        print(" ", action["part"], action["type"], "：", (action.get("ask") or "")[:50])
    parts = {action["part"] for action in actions if action.get("draft")}
    cancelled = [day["day"] for day in branch["candidate_days"] if day.get("cancelled")]
    steps = [(s["day"], s["kind"]) for o in branch["outcomes"] for s in o.get("next", [])]
    print("相手", sorted(parts), "取り消し", cancelled, "次の段", steps)
    branch["outcomes"] = [
        {**outcome, "actions": merge_by_part(outcome["actions"])} for outcome in branch["outcomes"]
    ]
    board = client.get("/api/board", headers=headers).json()
    accepted = client.post(
        "/api/board/branches",
        json={"version": board["version"], "branch": branch},
        headers=headers,
    )
    print("進める", accepted.status_code)
    if accepted.status_code != 201:
        return False
    saved = accepted.json()
    latest = saved["branches"][-1]["id"]
    family = [
        i for i in saved["items"] if i["part"] == "家族" and i["id"].startswith(f"s-{latest}")
    ]
    owners = {i.get("owner") for i in family}
    print("家族への文の宛先", owners)
    return (
        "家族" in parts
        and "職場" in parts
        and owners <= {"祖父母"}
        and any("16" in day for day in cancelled)
        and any(kind == "検査" and "17" in day for day, kind in steps)
    )


def run_home_b_scene(client: TestClient) -> bool:
    """家庭Bの場面（2026-11-07 作り直した家庭B：夫に頼めず、用事を頼める人は祖父母だけ）。

    採血がだめだったとき、祖父母に頼んだ木曜の迎えを外して祖父母に伝え直し、職場にも伝える。
    家族への文の宛先は祖父母だけ（夫に宛てない）。保育園・支援への案は出ない。
    祖父母が時刻つきで答えても、職場への連絡は返答待ちのまま残る（一往復が閉じる）。
    """
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
            " ",
            item["part"],
            item.get("owner"),
            item["status_label"],
            item["text_original"].replace("\n", " ")[:80],
        )
    family = next((item for item in made_items if item["part"] == "家族"), None)
    work = next((item for item in made_items if item["part"] == "職場"), None)
    parts = {item["part"] for item in made_items}
    if family is None or work is None:
        print("祖父母か職場への文がありません")
        return False
    set_status(client, headers, family["id"], "頼んだ")
    set_status(client, headers, work["id"], "頼んだ")
    replied = client.post(
        f"/api/board/items/{family['id']}/reply",
        json={"answer": "◯時なら", "when": "明日の朝"},
        headers=headers,
    ).json()
    family_now = next(item for item in replied["items"] if item["id"] == family["id"])
    work_now = next(item for item in replied["items"] if item["id"] == work["id"])
    print("祖父母", family_now["status_label"], "／職場", work_now["status_label"])
    return (
        not parts & {"保育園・預け先", "支援"}
        and {item.get("owner") for item in made_items if item["part"] == "家族"} <= {"祖父母"}
        and family_now["status_label"] == "明日の朝なら"
        and work_now["status_label"] == "返答待ち"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Claude APIを実際に呼ぶ")
    parser.add_argument("--own", type=int, default=0, help="作った家庭をこの回数通す")
    parser.add_argument("--shoot-only", type=int, default=0, help="撮影の順だけをこの回数通す")
    parser.add_argument("--shoot-b", type=int, default=0, help="家庭Bの撮影の順だけをこの回数通す")
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
        _follow_day()
        client = TestClient(create_app(engine, dist=Path(tmp)))
        if args.own:
            results = {
                f"{name}{n + 1}": run_own(client, name)
                for name in OWN_CHOICES
                for n in range(args.own)
            }
        elif args.shoot_b:
            results = {f"家庭Bの撮影の順{n + 1}": run_shoot_b(client) for n in range(args.shoot_b)}
        elif args.shoot_only:
            results = {f"撮影の順{n + 1}": run_shoot(client) for n in range(args.shoot_only)}
        else:
            results = {home: run_home(client, home) for home in ("home_a", "home_b")}
            results["撮影の順"] = run_shoot(client)
            results["家庭Bの撮影の順"] = run_shoot_b(client)
            results["家庭Bの場面"] = run_home_b_scene(client)
        engine.dispose()
    print(results)
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
