"""撮影のための手元のサーバー。ボードの「今日」だけを指定の日にする。

週の帯と週の表は今日から8日分を並べるので、話の順（木→月→火）は今日が月〜水曜のときだけ正しい。
公開先のコードは変えず、このサーバーの中でだけ日付を変える。データは一時フォルダのファイルに置く。
画面上部の日付はブラウザの時計なので、撮影する側で揃える。

既定は偽のAI（費用なし）。本物のAIは --real-ai を付けたときだけ呼ぶ。

使い方: python -X utf8 scripts/shoot_server.py --today 2026-11-04 [--real-ai] [--port 8010]
"""

import argparse
import os
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

import uvicorn

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS.parent / "backend"))
sys.path.insert(0, str(SCRIPTS))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--today", type=date.fromisoformat, required=True)
    parser.add_argument("--port", type=int, default=8010)
    parser.add_argument("--real-ai", action="store_true", help="Claude APIを実際に呼ぶ")
    args = parser.parse_args()

    # .env は環境変数より優先されるので、読み込みの後で手元のファイルへ向け直す。
    import ensemble.config

    folder = Path(tempfile.gettempdir())
    database = folder / "ensemble-shoot.db"
    os.environ["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    # eval の数字に撮影の呼び出しを混ぜない。記録の関数が読み込まれる前に向け直す。
    ensemble.config.AI_LOG_PATH = folder / "ensemble-shoot-ai-calls.jsonl"

    import ensemble.api

    today: date = args.today

    def now_jst() -> str:
        now = datetime.now(ensemble.api.JST)
        moved = now.replace(year=today.year, month=today.month, day=today.day)
        return moved.isoformat(timespec="seconds")

    ensemble.api.now_jst = now_jst
    ensemble.api.today_jst = lambda: today

    if args.real_ai:
        from ensemble.main import app
    else:
        from fake_ai_app import app

    ai = "本物（費用がかかります）" if args.real_ai else "偽（費用なし）"
    print(f"今日：{today:%Y-%m-%d}／AI：{ai}／データ：{database}", flush=True)
    print(f"撮影の入口：http://127.0.0.1:{args.port}/?shoot", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
