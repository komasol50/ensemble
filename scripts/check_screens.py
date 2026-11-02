"""画面の合格の検査。ビルドして手元でサーバーを立て、幅360pxで実際に操作して確かめる。

外へは何も送らない。Claude APIも呼ばない。DBは一時フォルダの SQLite。
使い方: python -X utf8 scripts/check_screens.py <写真の出力フォルダ> [--no-build]
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"
WIDTH, HEIGHT = 360, 740
MIN_TAP, MIN_GAP, MIN_FONT, MIN_CONTRAST = 48, 8, 16, 4.5

sys.path.insert(0, str(ROOT / "backend"))
from ensemble.guards import MEDICAL_FIXED_TEXT  # noqa: E402


def is_reddish(css_color: str) -> bool:
    """rgb(...) の赤が、緑と青より明らかに強いか。"""
    r, g, b = [float(v) for v in re.findall(r"[\d.]+", css_color)[:3]]
    return r > 120 and r > g * 1.6 and r > b * 1.6


MEASURE = """() => {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  const buttons = [...document.querySelectorAll('button, a[href]')].filter(visible);
  const rects = buttons.map((el) => {
    const r = el.getBoundingClientRect();
    return {text: el.innerText, top: r.top + scrollY, bottom: r.bottom + scrollY,
            left: r.left, right: r.right, h: r.height,
            primary: el.hasAttribute('data-primary')};
  });
  const rgb = (c) => {
    const m = c.match(/[\\d.]+/g).map(Number);
    return {r: m[0], g: m[1], b: m[2], a: m.length > 3 ? m[3] : 1};
  };
  const lum = ({r, g, b}) => {
    const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
  };
  const background = (el) => {
    for (let e = el; e; e = e.parentElement) {
      const c = rgb(getComputedStyle(e).backgroundColor);
      if (c.a === 1) return c;
    }
    return {r: 255, g: 255, b: 255, a: 1};
  };
  const texts = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    const node = walker.currentNode;
    const el = node.parentElement;
    if (!node.textContent.trim() || !el || !visible(el) || el.tagName === 'SCRIPT') continue;
    if (el.closest('button:disabled')) continue;
    const s = getComputedStyle(el);
    const a = lum(rgb(s.color)), b = lum(background(el));
    texts.push({text: node.textContent.trim().slice(0, 20), font: parseFloat(s.fontSize),
                contrast: (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05)});
  }
  return {rects, texts, scrollW: document.documentElement.scrollWidth,
          clientW: document.documentElement.clientWidth, bodyText: document.body.innerText};
}"""


class Report:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.passes = 0

    def check(self, ok: bool, what: str) -> None:
        if ok:
            self.passes += 1
        else:
            self.failures.append(what)
            print(f"NG: {what}")


def inspect(page: Page, name: str, out: Path, report: Report) -> None:
    """その画面の写真を撮り、画面の約束を数値で確かめる。"""
    # 全体の写真では、下に固定したボタンが途中に写り込むので、写真の間だけ一番下へ置く。
    page.screenshot(
        path=str(out / f"{name}.png"),
        full_page=True,
        style=".dock { position: static !important; }",
    )
    data = page.evaluate(MEASURE)
    report.check(data["scrollW"] <= data["clientW"], f"{name}: 横スクロールがある")
    rects = data["rects"]
    for rect in rects:
        report.check(rect["h"] >= MIN_TAP, f"{name}: 「{rect['text']}」の高さ {rect['h']:.0f}px")
        # 主なボタンは、開いた直後の画面の下半分に。スクロールの先にあるものは位置で測れない。
        if rect["primary"] and rect["top"] < HEIGHT:
            report.check(rect["top"] >= HEIGHT / 2, f"{name}: 「{rect['text']}」が上半分にある")
    for i, a in enumerate(rects):
        for b in rects[i + 1 :]:
            h_gap = max(a["left"], b["left"]) - min(a["right"], b["right"])
            v_gap = max(a["top"], b["top"]) - min(a["bottom"], b["bottom"])
            if max(h_gap, v_gap) < MIN_GAP:
                report.check(False, f"{name}: 「{a['text']}」と「{b['text']}」の間が狭い")
    for text in data["texts"]:
        report.check(text["font"] >= MIN_FONT, f"{name}: 「{text['text']}」が{text['font']}px")
        report.check(
            text["contrast"] >= MIN_CONTRAST,
            f"{name}: 「{text['text']}」のコントラスト {text['contrast']:.1f}",
        )


def request(method: str, path: str, code: str, body: dict | None = None) -> dict:
    """もう1台の端末の代わりに、APIを直接呼ぶ。"""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={"X-Family-Code": code, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as res:
        return json.loads(res.read())


def wait_for_server(process: subprocess.Popen) -> None:
    for _ in range(60):
        if process.poll() is not None:
            raise RuntimeError("サーバーが起動しませんでした")
        try:
            urllib.request.urlopen(f"{BASE}/health", timeout=1)
            return
        except OSError:
            time.sleep(0.5)
    raise RuntimeError("サーバーの起動を待ちきれませんでした")


def walk_through(page: Page, out: Path, report: Report) -> str:
    """最初の画面からボードを作り、入れて、状態を変えるまで。家族コードを返す。"""
    page.goto(BASE + "/", wait_until="networkidle")
    inspect(page, "1-start", out, report)

    page.get_by_role("button", name="家庭Aで始める").click()
    page.get_by_role("heading", name="職場").wait_for()
    code = page.evaluate("location.hash.slice(1)")
    report.check(len(code) >= 32, "家族コードがURLの # より後ろに無い")
    report.check(code not in page.evaluate("location.pathname"), "家族コードがパスに入っている")
    inspect(page, "2-board", out, report)

    body = page.locator("body").inner_text()
    for text in ("いま決めることはありません", "病院", "家族", "保育園・預け先", "職場", "未依頼",
                 "返答待ち", "引き受けた", "完了", "項目のない列：支援",
                 "用件だけ残し、原文は保存していません"):  # fmt: skip
        report.check(text in body, f"ボードに「{text}」が無い")
    report.check("パート" not in body, "「パート」が画面に出ている")
    report.check(
        page.locator("[data-item='a-04'] button", has_text="済").count() == 0,
        "完了の項目に済が出ている",
    )
    report.check(
        page.locator("[data-item='a-07'] button", has_text="済").count() == 0,
        "情報の項目に済が出ている",
    )
    columns = page.locator("main.board section h2").all_inner_texts()
    report.check(
        columns == ["いま決めること", "病院", "家族", "保育園・預け先", "職場"],
        f"列の順が違う: {columns}",
    )

    # 入れる：最初の1回だけ同意の3行。
    page.get_by_role("button", name="＋入れる").click()
    page.get_by_role("button", name="わかった").wait_for()
    inspect(page, "3-consent", out, report)
    page.get_by_role("button", name="わかった").click()
    box = page.get_by_role("textbox")
    inspect(page, "4-add-empty", out, report)
    report.check(
        page.get_by_role("button", name="入れる", exact=True).is_disabled(), "空で入れられる"
    )

    # 入れかけの文は、開き直しても残る。
    box.fill("先生：明日の採血しだい。")
    page.reload(wait_until="networkidle")
    report.check(
        page.get_by_role("textbox").input_value() == "先生：明日の採血しだい。",
        "入れかけの文が残っていない",
    )

    # 2,000字を超えたら切り捨てずに止める。
    page.get_by_role("textbox").fill("あ" * 2001)
    report.check(page.get_by_role("textbox").input_value() == "あ" * 2001, "貼った文が切られた")
    report.check(
        page.get_by_role("button", name="入れる", exact=True).is_disabled(), "超えても入れられる"
    )
    report.check("あと1字" in page.locator("body").inner_text(), "超えた字数が出ていない")
    inspect(page, "5-add-over", out, report)

    # 端末に先に保存：サーバーが落ちていても残る状態を、通信を止めて確かめる。
    page.context.set_offline(True)
    page.get_by_role("textbox").fill("先生：明日の採血しだい。よければ木曜に帰れるかも。")
    inspect(page, "6-add-filled", out, report)
    page.get_by_role("button", name="入れる", exact=True).click()
    # 通信が止まったままでも、入れた文と［＋入れる］は見える。読み込めていないことも正直に出す。
    page.get_by_text("仕分け中").first.wait_for()
    page.get_by_text("読み込めませんでした").wait_for()
    offline_text = page.locator("body").inner_text()
    report.check("よければ木曜に帰れるかも" in offline_text, "通信なしで入れた文が見えない")
    report.check(
        "いま決めることはありません" not in offline_text, "読めていないのに無いと言っている"
    )
    report.check(page.get_by_role("button", name="＋入れる").count() == 1, "通信なしで入れられない")
    inspect(page, "6a-board-offline", out, report)
    page.context.set_offline(False)
    page.get_by_role("button", name="もう一度読み込む").click()
    page.get_by_role("heading", name="職場").wait_for()
    report.check(
        "読み込めませんでした" not in page.locator("body").inner_text(), "戻っても消えない"
    )
    page.reload(wait_until="networkidle")
    report.check("仕分け中" in page.locator("body").inner_text(), "入れた文が開き直すと消える")
    report.check(
        "よければ木曜に帰れるかも" in page.locator("body").inner_text(),
        "入れた原文が出ていない",
    )
    inspect(page, "7-board-sorting", out, report)

    # もう1回押しても同意は出ない。
    page.get_by_role("button", name="＋入れる").click()
    report.check(page.get_by_role("textbox").count() == 1, "同意が2回出る")
    report.check(page.get_by_role("textbox").input_value() == "", "入れた後に文が残っている")
    page.get_by_role("button", name="戻る").click()

    # 「？」で同意の3行をいつでも読める。
    page.get_by_role("button", name="同意の内容を読む").click()
    page.get_by_role("button", name="閉じる").click()

    # 判断を求める問いには、内容によらず固定文をそのまま出す。赤を使わない。
    version = request("GET", "/api/board", code)["version"]
    added = request(
        "POST",
        "/api/board/items",
        code,
        {
            "version": version,
            "kind": "質問",
            "part": "病院",
            "certainty": "見込み",
            "text_original": "薬を増やしたほうがいいですか",
            "speaker": "親",
            "medical": "ask",
            "status": "未確認",
        },
    )
    asked_id = added["items"][-1]["id"]
    page.reload(wait_until="networkidle")
    asked = page.locator(f"[data-item='{asked_id}']")
    report.check(MEDICAL_FIXED_TEXT in asked.inner_text(), "医療の固定文がそのまま出ていない")
    color = asked.locator(".medical").evaluate(
        "el => [getComputedStyle(el).color, getComputedStyle(el).borderLeftColor]"
    )
    report.check(all(not is_reddish(value) for value in color), f"固定文に赤を使っている: {color}")
    inspect(page, "8a-board-medical", out, report)
    version = request("GET", "/api/board", code)["version"]
    request("DELETE", f"/api/board/items/{asked_id}?version={version}", code)
    page.reload(wait_until="networkidle")

    # 別の端末の変更に「新」が付く。
    version = request("GET", "/api/board", code)["version"]
    request("PATCH", "/api/board/items/a-02", code, {"version": version, "status": "引き受けた"})
    page.reload(wait_until="networkidle")
    card = page.locator("[data-item='a-02']")
    report.check("新" in card.inner_text(), "別の端末の変更に「新」が付かない")
    report.check(
        "新" not in page.locator("[data-item='a-03']").inner_text(), "変わらない項目に「新」"
    )
    inspect(page, "8-board-new", out, report)

    # 済を押すと完了になり、自分の変更には「新」が付かない。
    item = page.locator("[data-item='a-03']")
    item.get_by_role("button", name="済").click()
    item.get_by_text("完了").wait_for()
    report.check(item.get_by_role("button", name="済").count() == 0, "完了の後も済が残る")
    report.check("新" not in item.inner_text(), "自分の変更に「新」が付いた")

    # 別の端末が先に書いたら、失敗して読み直す（消えない）。
    version = request("GET", "/api/board", code)["version"]
    request("PATCH", "/api/board/items/a-06", code, {"version": version, "status": "頼んだ"})
    question = page.locator("[data-item='a-01']")
    question.get_by_role("button", name="済").click()
    page.get_by_text("ほかの端末で変わりました").wait_for()
    report.check("返答待ち" in page.locator("[data-item='a-06']").inner_text(), "読み直していない")
    report.check("未確認" in question.inner_text(), "古い版の更新が通った")
    inspect(page, "9-board-conflict", out, report)
    question.get_by_role("button", name="済").click()
    question.get_by_text("答えあり").wait_for()

    # 消すは2回押し。
    target = page.locator("[data-item='a-04']")
    target.get_by_role("button", name="消す").click()
    report.check(target.count() == 1, "1回押しで消えた")
    target.get_by_role("button", name="もう一度押すと消します").click()
    target.wait_for(state="detached")
    return code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path)
    parser.add_argument("--no-build", action="store_true")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    if not args.no_build:
        npm = shutil.which("npm") or "npm"
        subprocess.run(
            [npm, "run", "build"], cwd=ROOT / "frontend", check=True, capture_output=True
        )

    report = Report()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        log_path = Path(tmp) / "server.log"
        env = {**os.environ, "DATABASE_URL": f"sqlite:///{(Path(tmp) / 'check.db').as_posix()}"}
        with log_path.open("w", encoding="utf-8") as log:
            server = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "ensemble.main:app", "--app-dir", "backend",
                 "--port", str(PORT), "--log-level", "debug"],  # fmt: skip
                cwd=ROOT,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            try:
                wait_for_server(server)
                with sync_playwright() as p:
                    browser = p.chromium.launch()
                    context = browser.new_context(
                        viewport={"width": WIDTH, "height": HEIGHT},
                        device_scale_factor=2,
                        color_scheme="light",
                        has_touch=True,
                    )
                    page = context.new_page()
                    code = walk_through(page, args.out, report)
                    url = f"{BASE}/b#{code}"

                    # 暗い画面でも同じ検査。
                    dark = browser.new_context(
                        viewport={"width": WIDTH, "height": HEIGHT},
                        device_scale_factor=2,
                        color_scheme="dark",
                        has_touch=True,
                    )
                    dark_page = dark.new_page()
                    dark_page.goto(url, wait_until="networkidle")
                    dark_page.get_by_role("heading", name="職場").wait_for()
                    inspect(dark_page, "10-board-dark", args.out, report)
                    dark_page.evaluate("localStorage.setItem('ensemble.consented', 'false')")
                    dark_page.get_by_role("button", name="＋入れる").click()
                    inspect(dark_page, "11-consent-dark", args.out, report)
                    dark_page.get_by_role("button", name="わかった").click()
                    inspect(dark_page, "12-add-dark", args.out, report)

                    # 消えたボードを開いたとき。
                    home_b = browser.new_context(
                        viewport={"width": WIDTH, "height": HEIGHT},
                        device_scale_factor=2,
                        color_scheme="light",
                    )
                    b_page = home_b.new_page()
                    b_page.goto(BASE + "/", wait_until="networkidle")
                    b_page.get_by_role("button", name="家庭Bで始める").click()
                    b_page.get_by_role("heading", name="支援").wait_for()
                    report.check(
                        "18:30" in b_page.locator("body").inner_text(),
                        "家庭Bの延長保育の時刻が出ていない",
                    )
                    inspect(b_page, "14-board-home-b", args.out, report)

                    gone = browser.new_context(viewport={"width": WIDTH, "height": HEIGHT})
                    gone_page = gone.new_page()
                    gone_page.goto(f"{BASE}/b#{'x' * 43}", wait_until="networkidle")
                    gone_page.get_by_text("ボードが見つかりません").wait_for()
                    inspect(gone_page, "13-not-found", args.out, report)
                    browser.close()
            finally:
                server.terminate()
                server.wait(timeout=10)
        server_log = log_path.read_text(encoding="utf-8")
    report.check("GET /api/board" in server_log, "サーバーの記録を読めていない")
    report.check(code not in server_log, "家族コードがサーバーの記録に出ている")
    print(f"検査 {report.passes + len(report.failures)} 件／不合格 {len(report.failures)} 件")
    return 1 if report.failures else 0


if __name__ == "__main__":
    sys.exit(main())
