"""動画の静止画を 1920×1080 で作る。文字と図だけ。Claudeは呼ばない。

評価の数字は scripts/eval_numbers.py の出力（evals/numbers.json）だけを使う。
使い方: python -X utf8 scripts/make_slides.py
"""

import json
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "slides"
NUMBERS = ROOT / "evals" / "numbers.json"
PUBLIC_URL = "ensemble-8fmu.onrender.com"

LINE = "突然の子どもの入院でも、<br>家庭ごとに違う調整を、ひとりに背負わせない。"
LINE_EN = "Every family's hospital stay is different. No parent should have to carry the coordination alone."

STYLE = """
* { box-sizing: border-box; }
html, body { margin: 0; width: 1920px; height: 1080px; }
body {
  font-family: 'Noto Sans JP', 'Noto Sans CJK JP', 'Hiragino Sans', 'Yu Gothic UI', sans-serif;
  color: #eef0f5;
  background: #15181f;
}
.page {
  display: flex; flex-direction: column; justify-content: center; align-items: center;
  gap: 40px; width: 100%; height: 100%; padding: 120px; text-align: center;
}
.small { color: #aeb5c4; font-size: 44px; }
.name { font-size: 168px; font-weight: 700; letter-spacing: 0.04em; color: #c69ab5; }
.line { font-size: 56px; font-weight: 700; line-height: 1.5; }
.en { color: #aeb5c4; font-size: 38px; line-height: 1.4; }
.note { color: #aeb5c4; font-size: 34px; }
.url { color: #c69ab5; font-size: 48px; font-weight: 700; }
"""


def page(body: str, extra: str = "") -> str:
    return (
        '<!doctype html><html lang="ja"><head><meta charset="utf-8">'
        f"<style>{STYLE}{extra}</style></head><body>{body}</body></html>"
    )


def title() -> str:
    return page(f"""<div class="page">
          <div class="small">付き添い入院の負担軽減AI</div>
          <div class="name">アンサンブル</div>
          <div class="line">{LINE}</div>
          <div class="note">スマホだけで使える。インストールは要らない。</div>
        </div>""")


def stat() -> str:
    """絵の隅に重ねる数字。背景は透明。"""
    return page(
        """<div class="corner">
          <div class="num">75.0%</div>
          <div class="text">経験者調査の回答者の75.0%が、<br>病院から付き添いを求められた</div>
          <div class="src">付き添い白書2025</div>
        </div>""",
        """
        html, body { background: transparent; }
        .corner {
          position: absolute; left: 64px; bottom: 64px; padding: 32px 40px;
          border-radius: 20px; background: rgba(21, 24, 31, 0.72);
        }
        .num { color: #eef0f5; font-size: 88px; font-weight: 700; line-height: 1.1; }
        .text { margin-top: 8px; font-size: 34px; line-height: 1.5; }
        .src { margin-top: 8px; color: #aeb5c4; font-size: 26px; }
        """,
    )


def numbers() -> str:
    """評価の数字。値と分母は実測のファイルからだけ取る。"""
    data = json.loads(NUMBERS.read_text(encoding="utf-8"))
    notes = data.get("notes", {})
    cells = "".join(f"""<div class="cell">
          <div class="value">{entry['value']}<span>/{entry['total']}</span></div>
          <div class="label">{entry['label']}</div>
          {f'<div class="cell-note">{notes[entry["label"]]}</div>' if entry['label'] in notes else ''}
        </div>""" for entry in data["slide"])
    return page(
        f"""<div class="page">
          <div class="head">守りはコードで</div>
          <div class="cells">{cells}</div>
          <div class="note">合成データで測った、この試験の範囲での結果（{data['measured_on']}）</div>
        </div>""",
        """
        .head { font-size: 64px; font-weight: 700; }
        .cells { display: grid; grid-template-columns: repeat(3, 1fr); gap: 40px; width: 100%; }
        .cell { padding: 56px 32px; border-radius: 28px; background: #1f242e; }
        .value { color: #c69ab5; font-size: 136px; font-weight: 700; line-height: 1.1; }
        .value span { color: #aeb5c4; font-size: 72px; font-weight: 500; }
        .label { margin-top: 24px; font-size: 40px; line-height: 1.4; }
        .cell-note { margin-top: 12px; color: #aeb5c4; font-size: 28px; line-height: 1.4; }
        """,
    )


def vision() -> str:
    dots = "".join(
        f'<div class="dot"><i></i>{text}</div>'
        for text in (
            "退院日が動いた",
            "伝え直しの件数",
            "支援の空きがなかった",
            "病室に泊まれる人がほかにいない",
        )
    )
    return page(
        f"""<div class="page">
          <div class="flow">
            <div class="dots">{dots}</div>
            <div class="arrow"></div>
            <div class="box">NPO</div>
            <div class="arrow"></div>
            <div class="box strong">病院と制度</div>
          </div>
          <div class="caption">将来構想（今回は作っていない）</div>
          <div class="note">同意を得た分だけを件数で束ね、NPOを通じて病院と制度を変える材料に</div>
        </div>""",
        """
        .page { gap: 32px; }
        .flow { display: flex; align-items: center; gap: 32px; }
        .dots { display: grid; gap: 28px; text-align: left; }
        .dot { display: flex; align-items: center; gap: 20px; font-size: 40px; }
        .dot i { width: 28px; height: 28px; border-radius: 50%; background: #c69ab5; }
        .arrow { width: 96px; height: 6px; background: #aeb5c4; position: relative; }
        .arrow::after {
          content: ''; position: absolute; right: -2px; top: -12px; border-left: 24px solid #aeb5c4;
          border-top: 15px solid transparent; border-bottom: 15px solid transparent;
        }
        .box { padding: 40px 48px; border: 3px solid #aeb5c4; border-radius: 24px; font-size: 52px; }
        .box.strong { border-color: #c69ab5; color: #c69ab5; font-weight: 700; }
        .caption { margin-top: 48px; font-size: 48px; font-weight: 700; }
        """,
    )


def ending() -> str:
    return page(
        f"""<div class="page">
          <div class="name">アンサンブル</div>
          <div class="line">{LINE}</div>
          <div class="en">{LINE_EN}</div>
          <div class="url">{PUBLIC_URL}</div>
          <div class="note">試作・合成データのみ。医療の判断や心のケアはしません。</div>
        </div>""",
        ".name { font-size: 136px; }",
    )


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    slides = {
        "title": (title, False),
        "stat-75": (stat, True),
        "vision": (vision, False),
        "ending": (ending, False),
    }
    if NUMBERS.exists():
        slides["eval-numbers"] = (numbers, False)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        shot = browser.new_page(viewport={"width": 1920, "height": 1080})
        for name, (build, transparent) in slides.items():
            shot.set_content(build(), wait_until="load")
            shot.screenshot(path=str(OUT / f"{name}.png"), omit_background=transparent)
            print(f"{name}.png")
        browser.close()


if __name__ == "__main__":
    main()
