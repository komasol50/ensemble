"""撮影したボードの台帳を読む。Claudeを呼ばない。動画の小窓に出す「撮影時の実物」用。

家族コードをコマンドの引数やチャットに書かないよう、画面で入力する。
使い方: python -X utf8 scripts/shoot_dump.py 出力先.json [公開URL]
"""

import getpass
import json
import sys
import urllib.request
from pathlib import Path

DEFAULT_BASE = "https://ensemble-8fmu.onrender.com"
ITEM_FIELDS = (
    "kind",
    "part",
    "certainty",
    "condition",
    "medical",
    "speaker",
    "text_original",
    "task_summary",
    "refers_to",
    "status",
)


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    base = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_BASE
    # URLごと貼ってもよいように、# より後ろだけを使う。
    code = getpass.getpass("ボードのURLか家族コード（画面に出ません）: ").split("#")[-1].strip()
    request = urllib.request.Request(f"{base}/api/board", headers={"X-Family-Code": code})
    with urllib.request.urlopen(request) as response:
        board = json.load(response)
    shown = {
        "items": [{key: item.get(key) for key in ITEM_FIELDS} for item in board["items"]],
        "branches": board["branches"],
    }
    Path(sys.argv[1]).write_text(json.dumps(shown, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"項目 {len(shown['items'])} 件、案 {len(shown['branches'])} 件を書きました")
    return 0


if __name__ == "__main__":
    sys.exit(main())
