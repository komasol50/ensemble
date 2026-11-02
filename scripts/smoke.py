"""動いているサーバーの確認。Claudeは呼ばない（費用なし）。合成のボードを1つ作って消さずに残す。

ボード、項目、版の衝突、画面の配信を、本物のDBで確かめる。家族コードは表示しない。
使い方: python -X utf8 scripts/smoke.py https://xxx.onrender.com
"""

import json
import sys
import urllib.error
import urllib.request
from typing import Any


class Smoke:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        self.failures = 0

    def request(
        self, method: str, path: str, *, code: str | None = None, body: Any = None
    ) -> tuple[int, str, dict[str, str]]:
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json", "User-Agent": "ensemble-smoke"}
        if code:
            headers["X-Family-Code"] = code
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=120) as res:
                return res.status, res.read().decode(), dict(res.headers)
        except urllib.error.HTTPError as error:
            return error.code, error.read().decode(), dict(error.headers)

    def check(self, ok: bool, what: str) -> None:
        print(("OK  " if ok else "NG  ") + what)
        self.failures += not ok


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    smoke = Smoke(sys.argv[1])

    status, body, _ = smoke.request("GET", "/health")
    smoke.check(status == 200 and json.loads(body)["status"] == "ok", "health（DBにつながる）")

    status, body, _ = smoke.request("GET", "/")
    smoke.check(status == 200 and '<div id="root">' in body, "画面が配られる")
    status, _, _ = smoke.request("GET", "/api/nothing")
    smoke.check(status == 404, "知らないAPIは404")
    for path in ("/.env", "/env.example", "/data/presets/home_a.json"):
        status, body, _ = smoke.request("GET", path)
        smoke.check(
            "ANTHROPIC_API_KEY" not in body and '"profile"' not in body, f"{path} が読めない"
        )

    status, body, headers = smoke.request("POST", "/api/boards", body={"home": "home_b"})
    smoke.check(status == 201, "ボードを作れる")
    made = json.loads(body)
    code, board = made["code"], made["board"]
    smoke.check(
        headers.get("Cache-Control", headers.get("cache-control")) == "no-store",
        "応答を保存させない",
    )
    smoke.check(code not in json.dumps(board), "家族コードを本文に返さない")

    status, body, _ = smoke.request("GET", "/api/board", code=code)
    smoke.check(status == 200 and json.loads(body)["version"] == 1, "読み直せる（DBに残る）")
    status, _, _ = smoke.request("GET", "/api/board")
    smoke.check(status == 404, "コードなしでは読めない")

    status, body, _ = smoke.request(
        "POST",
        "/api/board/items",
        code=code,
        body={
            "version": 1,
            "kind": "行動",
            "part": "家族",
            "certainty": "確定",
            "text_original": "土曜に洗濯物を持ち帰ってほしい",
            "speaker": "親",
            "status": "頼みたい",
        },
    )
    smoke.check(status == 201, "項目を足せる")
    item_id = json.loads(body)["items"][-1]["id"]
    status, _, _ = smoke.request(
        "PATCH", f"/api/board/items/{item_id}", code=code, body={"version": 1, "status": "頼んだ"}
    )
    smoke.check(status == 409, "古い版からの更新は失敗する")
    status, body, _ = smoke.request(
        "PATCH", f"/api/board/items/{item_id}", code=code, body={"version": 2, "status": "頼んだ"}
    )
    smoke.check(status == 200 and json.loads(body)["version"] == 3, "今の版からは更新できる")
    status, body, _ = smoke.request("DELETE", f"/api/board/items/{item_id}?version=3", code=code)
    smoke.check(status == 200 and json.loads(body)["version"] == 4, "項目を消せる")

    print(f"不合格 {smoke.failures} 件")
    return 1 if smoke.failures else 0


if __name__ == "__main__":
    sys.exit(main())
