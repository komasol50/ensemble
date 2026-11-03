"""画面の合格の検査。ビルドして手元でサーバーを立て、幅360pxで実際に操作して確かめる。

外へは何も送らない。Claude APIも呼ばない（scripts/fake_ai_app.py の偽の応答を使う）。
DBは一時フォルダの SQLite。
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
import urllib.error
import urllib.request
import struct
import zlib
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"
WIDTH, HEIGHT = 360, 740
# 本文は16px、ラベルは15px（画面デザインの決定）。
MIN_TAP, MIN_GAP, MIN_FONT, MIN_CONTRAST = 48, 8, 15, 4.5
SORTED_TEXT = "先生：今週はこのまま様子を見ます"  # 初期の項目に含まれない文

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
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'
      && el.checkVisibility();
  };
  const buttons = [...document.querySelectorAll('button, a[href]')].filter(visible);
  const rects = buttons.map((el) => {
    const r = el.getBoundingClientRect();
    return {text: el.innerText, top: r.top + scrollY, bottom: r.bottom + scrollY,
            left: r.left, right: r.right, h: r.height,
            primary: el.hasAttribute('data-primary'), docked: !!el.closest('.dock'),
            inView: r.top < innerHeight && r.bottom > 0};
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


# 一番下までスクロールしたときの、本文の最後のボタンと固定のボタンの間。固定が無ければ None。
END_GAP = """() => {
  const dock = document.querySelector('.dock button');
  if (!dock) return null;
  const y = scrollY;
  const main = document.querySelector('main');
  const inner = main ? main.scrollTop : 0;
  scrollTo(0, document.documentElement.scrollHeight);
  if (main) main.scrollTop = main.scrollHeight;
  const top = dock.getBoundingClientRect().top;
  const others = [...document.querySelectorAll('main button')].filter((el) => el.checkVisibility());
  const bottom = others.length
    ? Math.max(...others.map((el) => el.getBoundingClientRect().bottom)) : -Infinity;
  scrollTo(0, y);
  if (main) main.scrollTop = inner;
  return bottom === -Infinity ? null : Math.round(top - bottom);
}"""


def synthetic_png(width: int = 300, height: int = 400) -> bytes:
    """検査用の合成の画像。実在の資料は使わない。"""

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    rows = b"".join(
        b"\x00" + bytes((200, 220, 210) if y % 20 < 10 else (240, 240, 235)) * width
        for y in range(height)
    )
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


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
    # 主なボタンは、スクロールしなくても1つは見える（「確かめるが出ない」の再発を防ぐ）。
    primaries = [rect for rect in rects if rect["primary"]]
    report.check(
        not primaries or any(rect["inView"] for rect in primaries),
        f"{name}: 主なボタンが画面の外にしかない",
    )
    for rect in rects:
        report.check(rect["h"] >= MIN_TAP, f"{name}: 「{rect['text']}」の高さ {rect['h']:.0f}px")
        # 主なボタンは、開いた直後の画面の下半分に。スクロールの先にあるものは位置で測れない。
        if rect["primary"] and rect["top"] < HEIGHT:
            report.check(rect["top"] >= HEIGHT / 2, f"{name}: 「{rect['text']}」が上半分にある")
    for i, a in enumerate(rects):
        for b in rects[i + 1 :]:
            # 下に固定したボタンは、スクロールの途中で本文と重なる。下の END_GAP で確かめる。
            if a["docked"] != b["docked"]:
                continue
            h_gap = max(a["left"], b["left"]) - min(a["right"], b["right"])
            v_gap = max(a["top"], b["top"]) - min(a["bottom"], b["bottom"])
            if max(h_gap, v_gap) < MIN_GAP:
                report.check(False, f"{name}: 「{a['text']}」と「{b['text']}」の間が狭い")
    gap = page.evaluate(END_GAP)
    report.check(
        gap is None or gap >= MIN_GAP,
        f"{name}: 一番下までスクロールしても、固定のボタンが本文に重なる（間 {gap}px）",
    )
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


def add_text(page: Page, text: str) -> None:
    page.get_by_role("button", name="＋情報を追加する").click()
    page.get_by_role("textbox").fill(text)
    page.get_by_role("button", name="追加する", exact=True).click()


def open_parts(page: Page) -> None:
    """タイルを押して相手のカードを開く。開いた状態は端末に残る。"""
    for tile in page.locator("[data-tile]").all():
        if tile.is_enabled():
            tile.click()
    page.wait_for_timeout(500)
    page.evaluate("scrollTo(0, 0)")
    page.wait_for_timeout(200)


def open_sends(page: Page) -> None:
    """畳まれた「まとめて送る」を開く。"""
    box = page.locator("details#sends")
    box.wait_for()
    if box.get_attribute("open") is None:
        box.locator("> summary").click()


def within_height(page: Page, name: str, screens: int, report: Report) -> None:
    """スクロールの量が増えすぎないこと。"""
    height = page.evaluate("document.documentElement.scrollHeight")
    report.check(height <= screens * HEIGHT, f"{name}: 高さ {height}px（{screens}画面まで）")


def fixed_note(page: Page, name: str, report: Report) -> None:
    """医療の固定文が全文で、一番下までスクロールすると下の帯に隠れない。"""
    note = page.locator(".fixed-note")
    report.check(note.count() == 1, f"{name}: 固定文が{note.count()}個")
    report.check(note.inner_text() == MEDICAL_FIXED_TEXT, f"{name}: 固定文が全文でない")
    gap = page.evaluate("""() => {
      const y = scrollY;
      scrollTo(0, document.documentElement.scrollHeight);
      const note = document.querySelector('.fixed-note').getBoundingClientRect();
      const dock = document.querySelector('.dock');
      const limit = dock ? dock.getBoundingClientRect().top : innerHeight;
      scrollTo(0, y);
      return limit - note.bottom;
    }""")
    report.check(gap >= 0, f"{name}: 固定文が帯に隠れる（{gap}px）")


def walk_through(page: Page, out: Path, report: Report) -> str:
    """最初の画面からボードを作り、入れて、状態を変えるまで。家族コードを返す。"""
    page.goto(BASE + "/", wait_until="networkidle")
    inspect(page, "1-start", out, report)

    page.get_by_role("button", name="家庭Aで始める").click()
    page.locator("[data-tile='職場']").wait_for()
    code = page.evaluate("location.hash.slice(1)")
    report.check(len(code) >= 32, "家族コードがURLの # より後ろに無い")
    report.check(code not in page.evaluate("location.pathname"), "家族コードがパスに入っている")
    inspect(page, "2-board", out, report)
    # 作った直後に、URLを自分へ送る案内が出る。閉じたら開き直しても出ない。
    save_link = page.locator("[data-save-link]")
    report.check(save_link.count() == 1, "URLを送る案内が出ていない")
    save_link.get_by_role("button", name="あとで").click()
    page.reload(wait_until="networkidle")
    page.locator("[data-tile='職場']").wait_for()
    report.check(save_link.count() == 0, "閉じた案内がまた出た")
    within_height(page, "2-board", 2, report)
    # この家の事情は畳んである。開くと3枚。
    home_cards = page.locator("[data-home-cards]")
    home_cards.locator("> summary").click()
    report.check(home_cards.locator(".card").count() == 3, "この家の事情が3枚でない")
    report.check("夫（" in home_cards.inner_text(), "用事を頼める人が出ていない")
    inspect(page, "2c-board-home", out, report)
    home_cards.locator("> summary").click()
    # 初めは、返事のある相手だけ開いている。文は押すと読める。
    report.check(
        page.get_by_role("heading", name="職場").count() == 0, "閉じたはずの相手が開いている"
    )
    report.check(
        page.get_by_role("heading", name="家族").count() == 1, "返事のある相手が閉じている"
    )

    body = page.locator("body").inner_text()
    for text in ("いま決めることはありません", "病院", "家族", "保育園・預け先", "職場", "未依頼",
                 "返答待ち", "返事あり", "引き受けた", "済"):  # fmt: skip
        report.check(text in body, f"ボードに「{text}」が無い")
    report.check("パート" not in body, "「パート」が画面に出ている")
    open_parts(page)
    report.check(
        "用件だけ残し、原文は保存していません" in page.locator("[id='part-職場']").inner_text(),
        "第三者の文の知らせが読めない",
    )
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
        columns == ["病院", "家族", "保育園・預け先", "職場"],
        f"列の順が違う: {columns}",
    )

    # 画面デザインの決定：色・タイル・固定文。
    primary_color = page.get_by_role("button", name="＋情報を追加する").evaluate(
        "el => getComputedStyle(el).backgroundColor"
    )
    report.check(primary_color == "rgb(110, 74, 105)", f"主色が葡萄でない: {primary_color}")
    tiles = page.locator("[data-tile]")
    report.check(
        tiles.evaluate_all("els => els.map((el) => el.dataset.tile)")
        == ["病院", "家族", "保育園・預け先", "職場", "支援"],
        "タイルの順が違う",
    )
    report.check(
        "なし" in page.locator("[data-tile='支援']").inner_text(), "空の相手に「なし」が無い"
    )
    report.check(page.locator("[data-tile='支援']").is_disabled(), "空のタイルが押せる")
    tile_text = " ".join(tiles.all_inner_texts())
    report.check(
        "完了" not in tile_text and "未確認" not in tile_text, f"状態の語が4つ以外: {tile_text}"
    )
    page.locator("[data-tile='職場']").click()
    page.wait_for_timeout(800)
    top = page.get_by_role("heading", name="職場").evaluate("el => el.getBoundingClientRect().top")
    report.check(0 <= top < HEIGHT / 2, f"タイルを押しても相手のカードへ動かない（{top}px）")
    page.evaluate("scrollTo(0, 0)")
    page.wait_for_timeout(300)
    fixed_note(page, "ボード", report)

    # 進捗別：4つの語で並ぶ。済は畳んでおく。
    page.get_by_role("tab", name="進捗別").click()
    groups = page.locator("details[data-state]")
    report.check(
        groups.evaluate_all("els => els.map((el) => el.dataset.state)")
        == ["未依頼", "返答待ち", "返事あり", "済"],
        "進捗別の順が違う",
    )
    report.check(
        page.locator("details[data-state='済']").get_attribute("open") is None, "済が開いている"
    )
    inspect(page, "2a-board-progress", out, report)
    fixed_note(page, "進捗別", report)

    # 週の表：マスに原文が出る。今日と、その先7日。
    page.get_by_role("tab", name="週の予定").click()
    inspect(page, "2b-board-week", out, report)
    rows = page.locator("table.week tbody tr")
    report.check(rows.count() == 8, f"週の表の行が{rows.count()}行")
    report.check("今日" in rows.first.inner_text(), "週の表の先頭が今日でない")
    week_text = page.locator("table.week").inner_text()
    report.check("明日の採血しだい" in week_text, "週の表に医師の言葉が無い")
    report.check("木曜の会議" in week_text, "週の表に職場の予定が無い")
    page.get_by_role("tab", name="相手別").click()
    report.check(
        page.get_by_role("button", name="次にやることを見る").count() == 0,
        "案が無いのに地図の入口が出ている",
    )

    # 入れる：最初の1回だけ同意の3行。
    page.get_by_role("button", name="＋情報を追加する").click()
    page.get_by_role("button", name="わかった").wait_for()
    inspect(page, "3-consent", out, report)
    page.get_by_role("button", name="わかった").click()
    box = page.get_by_role("textbox")
    inspect(page, "4-add-empty", out, report)
    report.check(
        page.get_by_role("button", name="追加する", exact=True).is_disabled(), "空で入れられる"
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
        page.get_by_role("button", name="追加する", exact=True).is_disabled(), "超えても入れられる"
    )
    report.check("あと1字" in page.locator("body").inner_text(), "超えた字数が出ていない")
    inspect(page, "5-add-over", out, report)

    # 端末に先に保存：サーバーが落ちていても残る状態を、通信を止めて確かめる。
    page.context.set_offline(True)
    page.get_by_role("textbox").fill(SORTED_TEXT)
    inspect(page, "6-add-filled", out, report)
    page.get_by_role("button", name="追加する", exact=True).click()
    # 通信が止まったままでも、入れた文と［＋入れる］は見える。読み込めていないことも正直に出す。
    page.get_by_text("仕分け中").first.wait_for()
    page.get_by_text("読み込めませんでした").wait_for()
    offline_text = page.locator("body").inner_text()
    report.check(SORTED_TEXT in offline_text, "通信なしで入れた文が見えない")
    report.check(
        "いま決めることはありません" not in offline_text, "読めていないのに無いと言っている"
    )
    report.check(
        page.get_by_role("button", name="＋情報を追加する").count() == 1, "通信なしで入れられない"
    )
    inspect(page, "6a-board-offline", out, report)
    page.context.set_offline(False)
    page.get_by_role("button", name="もう一度読み込む").click()
    page.locator("[data-tile='職場']").wait_for()
    report.check(
        "読み込めませんでした" not in page.locator("body").inner_text(), "戻っても消えない"
    )
    # つながったら仕分けが進む。途中で開き直しても文は消えず、2回入らない。
    page.get_by_text("仕分け中").first.wait_for()
    inspect(page, "7-board-sorting", out, report)
    page.reload()
    report.check(SORTED_TEXT in page.locator("body").inner_text(), "開き直すと消える")
    page.get_by_text("仕分け中").first.wait_for(state="detached")
    page.locator("[data-item]", has_text=SORTED_TEXT).first.wait_for()
    page.wait_for_timeout(2500)  # 開き直す前の呼び出しが終わるのを待つ
    page.reload(wait_until="networkidle")
    sorted_count = page.locator("[data-item]", has_text=SORTED_TEXT).count()
    report.check(sorted_count == 1, f"入れた文が{sorted_count}件になった")
    report.check("仕分け中" not in page.locator("body").inner_text(), "仕分けが終わらない")

    # もう1回押しても同意は出ない。
    page.get_by_role("button", name="＋情報を追加する").click()
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
    item.locator(".chips").get_by_text("済", exact=True).wait_for()
    report.check(item.get_by_role("button", name="済").count() == 0, "完了の後も済が残る")
    report.check("新" not in item.inner_text(), "自分の変更に「新」が付いた")

    # 別の端末が先に書いたら、失敗して読み直す（消えない）。
    version = request("GET", "/api/board", code)["version"]
    # 3秒ごとの読み直しが先に受け取ると衝突にならないので、押して失敗するまで止める。
    blocked = {"on": True}

    def hold_polling(route) -> None:
        if blocked["on"] and route.request.method == "GET":
            route.abort()
        elif route.request.method == "PATCH":
            response = route.fetch()
            blocked["on"] = False
            route.fulfill(response=response)
        else:
            route.continue_()

    page.route("**/api/board**", hold_polling)
    request("PATCH", "/api/board/items/a-06", code, {"version": version, "status": "頼んだ"})
    question = page.locator("[data-item='a-01']")
    question.get_by_role("button", name="済").click()
    page.get_by_text("ほかの端末で変わりました").wait_for()
    page.unroute("**/api/board**")
    # 知らせを出してから読み直すので、読み直しが終わるのを待つ。
    page.locator("[data-item='a-06']").get_by_text("返答待ち").wait_for(timeout=5000)
    report.check("未依頼" in question.inner_text(), "古い版の更新が通った")
    inspect(page, "9-board-conflict", out, report)
    question.get_by_role("button", name="済").click()
    question.locator(".chips").get_by_text("済", exact=True).wait_for()

    # 消す：消す前に必ず聞く。やめたら何も変わらない。
    def item_ids() -> set[str]:
        return {item["id"] for item in request("GET", "/api/board", code)["items"]}

    target = page.locator("[data-item='a-04']")
    confirm = page.locator(".dock-note.confirm")
    target.get_by_role("button", name="消す").click()
    confirm.get_by_text("を消しますか").wait_for()
    report.check("充電器" in confirm.inner_text(), "何を消すのかが確認に出ていない")
    report.check(target.count() == 1, "聞く前に画面から消えた")
    report.check("a-04" in item_ids(), "聞く前に台帳から消えた")
    inspect(page, "9a-board-confirm", out, report)
    confirm.get_by_role("button", name="やめる").click()
    confirm.wait_for(state="detached")
    report.check(target.count() == 1 and "a-04" in item_ids(), "やめたのに消えた")

    # 「消す」を押すとゴミ箱へ。
    target.get_by_role("button", name="消す").click()
    confirm.get_by_role("button", name="消す", exact=True).click()
    target.wait_for(state="detached")
    page.get_by_text("ゴミ箱に入れました").wait_for()
    report.check("a-04" not in item_ids(), "台帳から消えていない")

    # ゴミ箱から戻せる。開き直した後でも。
    page.reload(wait_until="networkidle")
    page.get_by_role("button", name="ゴミ箱を開く").click()
    trashed = page.locator("[data-trash='a-04']")
    trashed.wait_for()
    inspect(page, "9b-trash", out, report)
    trashed.get_by_role("button", name="元に戻す").click()
    page.get_by_text("元に戻しました").wait_for()
    report.check("a-04" in item_ids(), "戻しても台帳に無い")
    report.check(trashed.count() == 0, "戻した後もゴミ箱に残っている")
    page.get_by_role("button", name="戻る").click()
    target.wait_for()
    report.check("済" in target.inner_text(), "戻した項目の状態が違う")

    # 完全に消すと戻せない（2回押し）。
    target.get_by_role("button", name="消す").click()
    confirm.get_by_role("button", name="消す", exact=True).click()
    target.wait_for(state="detached")
    page.get_by_role("button", name="ゴミ箱を開く").click()
    trashed.wait_for()
    trashed.get_by_role("button", name="完全に消す").click()
    report.check(trashed.count() == 1, "1回押しで完全に消えた")
    trashed.get_by_role("button", name="もう一度押すと、戻せなくなります").click()
    page.get_by_text("完全に消しました").wait_for()
    report.check(trashed.count() == 0, "完全に消えていない")
    page.get_by_role("button", name="戻る").click()
    page.locator("[data-tile='職場']").wait_for()

    add_text(page, "薬を増やしたほうがいいですか")
    asked = page.locator("[data-item]", has_text="薬を増やしたほうがいいですか").first
    asked.wait_for()
    report.check(MEDICAL_FIXED_TEXT in asked.inner_text(), "仕分けた問いに固定文が出ていない")
    report.check(
        page.get_by_role("button", name="確かめる").count() == 0, "医療の問いから案が作られた"
    )

    # 第三者の文は原文を残さない。
    add_text(page, "上司：会議はどうしますか")
    boss = page.locator("[data-item]", has_text="会議の連絡").last
    boss.wait_for()
    report.check("原文は保存していません" in boss.text_content(), "第三者の文の知らせが無い")
    report.check("どうしますか" not in boss.text_content(), "第三者の原文が残っている")

    # 親だけの用事も画面から消えない。案にならない文でも、どこへ入ったかを知らせる。
    add_text(page, "売店で買う")
    page.locator("[data-tile='親']").click()
    page.get_by_role("heading", name="自分のこと").wait_for()
    page.get_by_text("「自分のこと」に入れました").wait_for()
    inspect(page, "15-board-parent", out, report)

    # 親が選べること：任意で、選んだら相手の欄が出る。キーボードで狭くても押せる。
    page.get_by_role("button", name="＋情報を追加する").click()
    page.get_by_text("選ばなくても追加できます").wait_for()
    report.check(page.get_by_text("誰に").count() == 0, "選ぶ前に相手の欄が出ている")
    inspect(page, "15a-add-choices", out, report)
    page.get_by_role("button", name="聞きたい").click()
    page.get_by_text("誰に").wait_for()
    page.get_by_role("button", name="夫").click()
    report.check(
        page.get_by_role("button", name="夫").get_attribute("aria-pressed") == "true",
        "選んだ印が付かない",
    )
    page.get_by_role("textbox").fill("着替えの場所を教えて")
    inspect(page, "15b-add-chosen", out, report)
    page.set_viewport_size({"width": WIDTH, "height": 420})
    submit = page.get_by_role("button", name="追加する", exact=True)
    submit.scroll_into_view_if_needed()
    box = submit.bounding_box()
    report.check(box is not None and box["y"] + box["height"] <= 420, "狭い画面で追加を押せない")
    page.set_viewport_size({"width": WIDTH, "height": HEIGHT})
    submit.click()
    chosen = page.locator("[data-item]", has_text="着替えの場所を教えて").first
    chosen.wait_for()
    saved = next(
        item
        for item in request("GET", "/api/board", code)["items"]
        if item["text_original"] == "着替えの場所を教えて"
    )
    report.check(
        (saved["kind"], saved["part"], saved["speaker"]) == ("質問", "家族", "親"),
        f"選んだ内容が当たっていない: {saved['kind']} {saved['part']} {saved['speaker']}",
    )
    # 別の相手を選んで戻っても、次の入力に持ち越さない。
    page.get_by_role("button", name="＋情報を追加する").click()
    report.check(page.get_by_text("誰に").count() == 0, "前回の選択が残っている")
    page.get_by_role("button", name="戻る").click()

    # 仕分けを断られても、文は残り、もう一度押せる。
    add_text(page, "失敗する文")
    page.get_by_text("仕分けできませんでした").wait_for()
    inspect(page, "16-board-sort-failed", out, report)
    report.check("失敗する文" in page.locator("body").inner_text(), "断られた文が消えた")
    failed = page.locator(".card.sorting", has_text="失敗する文")
    failed.get_by_role("button", name="もう一度仕分ける").click()
    failed.get_by_text("仕分け中").wait_for()
    failed.get_by_text("仕分けできませんでした").wait_for()
    failed.get_by_role("button", name="消す").click()
    failed.get_by_role("button", name="もう一度押すと消します").click()
    failed.wait_for(state="detached")

    # 通信が1回失敗しただけなら、切り替えの知らせが無くても進む（実機で「出ない」の原因）。
    calls = {"n": 0}

    def fail_once(route) -> None:
        calls["n"] += 1
        route.abort() if calls["n"] == 1 else route.continue_()

    page.route("**/api/board/proposal", fail_once)
    add_text(page, "今日の採血はだめでした")
    page.get_by_role("button", name="確かめる").wait_for(timeout=15000)
    page.unroute("**/api/board/proposal")
    report.check(calls["n"] >= 2, "送り直していない")

    # スクロールしていても［確かめる］が見える（実機で「出ない」の原因）。
    page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
    page.wait_for_timeout(200)
    box = page.get_by_role("button", name="確かめる").bounding_box()
    report.check(0 <= box["y"] < HEIGHT, f"スクロールすると［確かめる］が見えない（{box['y']}）")
    page.evaluate("window.scrollTo(0, 0)")
    inspect(page, "16a-board-ready", out, report)
    page.get_by_role("button", name="確かめる").click()
    page.get_by_role("button", name="今は何もしない").click()
    page.get_by_text("いま決めることはありません").wait_for()

    # 閉じた案が、遅れて届いた応答で戻ってこない（実機で「出たり出なかったり」の原因）。
    held: list = []

    def hold_first(route) -> None:
        if not held:
            held.append(route)
        else:
            route.continue_()

    page.route("**/api/board/proposal", hold_first)
    add_text(page, "今日の採血はだめでした")
    page.get_by_text("案を作っています").first.wait_for()
    while not held:
        page.wait_for_timeout(100)
    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    page.get_by_role("button", name="確かめる").wait_for()
    page.get_by_role("button", name="確かめる").click()
    page.get_by_role("button", name="今は何もしない").click()
    page.get_by_text("いま決めることはありません").wait_for()
    held[0].continue_()
    page.wait_for_timeout(3000)
    page.unroute("**/api/board/proposal")
    report.check(page.get_by_role("button", name="確かめる").count() == 0, "閉じた案が戻ってきた")

    # 別のアプリへ切り替えて通信が切れても、戻れば「確かめる」まで進む。
    def back_to_app() -> None:
        page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")

    page.route("**/api/board/proposal", lambda route: route.abort())
    add_text(page, "今日の採血はだめでした")
    page.get_by_text("案を作っています").first.wait_for()
    page.wait_for_timeout(500)
    page.unroute("**/api/board/proposal")
    back_to_app()
    page.get_by_role("button", name="確かめる").wait_for()
    page.get_by_role("button", name="確かめる").click()
    page.get_by_role("button", name="今は何もしない").click()
    page.get_by_text("いま決めることはありません").wait_for()

    # 応答が返らないままでも、戻れば進む。
    page.route("**/api/board/proposal", lambda route: None)
    add_text(page, "今日の採血はだめでした")
    page.get_by_text("案を作っています").first.wait_for()
    page.unroute("**/api/board/proposal")
    back_to_app()
    page.get_by_role("button", name="確かめる").wait_for()
    page.get_by_role("button", name="確かめる").click()
    page.get_by_role("button", name="今は何もしない").click()
    page.get_by_text("いま決めることはありません").wait_for()

    # 仕分けの応答が届かず送り直しても、2回入らず、案まで進む。
    page.route("**/api/board/intake", lambda route: route.fetch() and route.abort())
    before_count = len(request("GET", "/api/board", code)["items"])
    add_text(page, "今日の採血はだめでした")
    page.wait_for_timeout(1500)
    page.unroute("**/api/board/intake")
    back_to_app()
    page.get_by_role("button", name="確かめる").wait_for()
    after_count = len(request("GET", "/api/board", code)["items"])
    report.check(after_count == before_count + 1, f"送り直しで{after_count - before_count}件入った")
    page.get_by_role("button", name="確かめる").click()
    page.get_by_role("button", name="今は何もしない").click()
    page.get_by_text("いま決めることはありません").wait_for()

    # 1つの問い：［今は何もしない］は台帳を変えない。
    add_text(page, "今日の採血はだめでした")
    page.get_by_text("案を作っています").first.wait_for()
    page.get_by_role("button", name="確かめる").wait_for()
    before = request("GET", "/api/board", code)
    page.get_by_role("button", name="確かめる").click()
    page.get_by_role("button", name="この案で進める").wait_for()
    inspect(page, "17-decide", out, report)
    fixed_note(page, "いま決めること", report)
    report.check(page.locator(".plan-toggle").count() == 0, "普段から札が押せる")
    badge = page.locator(".badge.change").first.evaluate(
        "el => getComputedStyle(el).backgroundColor"
    )
    report.check(badge == "rgba(0, 0, 0, 0)", f"押せない札が塗られている: {badge}")
    report.check(page.locator("mark.decide-word").count() >= 1, "決める言葉に印が付いていない")
    # 長い案の文は2行に畳み、押すと全文を読める。
    long_draft = page.locator("[data-action='職場'] details.plan-fold")
    report.check(long_draft.count() == 1, "長い案の文が畳まれていない")
    draft_text = long_draft.locator(".plan-text")
    folded = draft_text.evaluate("el => el.getBoundingClientRect().height")
    report.check(folded <= 60, f"畳んだ案の文の高さ {folded}px")
    report.check("全文を読む" in long_draft.inner_text(), "開ける印が無い")
    long_draft.locator("summary").click()
    opened = draft_text.evaluate("el => el.getBoundingClientRect().height")
    report.check(opened > folded, "押しても案の文が開かない")
    report.check("会議の件" in long_draft.inner_text(), "全文が読めない")
    long_draft.locator("summary").click()
    body = page.locator("body").inner_text()
    for text in ("今日の採血はだめでした", "よければ木曜に帰れるかも", "取り消し", "候補",
                 "こう伝え直しますか", "外す：木曜の延長の空きを保育園に確認した"):  # fmt: skip
        report.check(text in body, f"問いの画面に「{text}」が無い")
    page.get_by_role("button", name="今は何もしない").click()
    page.get_by_text("いま決めることはありません").wait_for()
    report.check(request("GET", "/api/board", code) == before, "何もしないで台帳が変わった")

    # 開いて戻っても案は残り、一部を外して進められる。押す前は状態が変わらない。
    add_text(page, "今日の採血はだめでした")
    page.get_by_role("button", name="確かめる").wait_for()
    page.get_by_role("button", name="確かめる").click()
    page.get_by_role("button", name="ボード", exact=True).click()
    page.get_by_role("button", name="確かめる").click()
    page.get_by_role("button", name="一部だけ変える").click()
    work_row = page.locator("[data-action='職場'] .plan-toggle")
    report.check("変える" in work_row.inner_text(), "切り替える前の札が違う")
    work_row.click()
    report.check("そのまま" in work_row.inner_text(), "押しても「そのまま」にならない")
    report.check(work_row.get_attribute("aria-pressed") == "false", "切り替えの状態が読めない")
    report.check(
        page.get_by_role("button", name="この案で進める", exact=True).count() == 1,
        "進めるボタンの文言が変わった",
    )
    inspect(page, "18-decide-partial", out, report)
    # 文を直せる。医療の語が入ると送れない。親が書いた日付は通る。
    family_row = page.locator("[data-action='家族']")
    family_row.get_by_role("button", name="文を直す").click()
    edited_box = family_row.get_by_role("textbox")
    original_draft = edited_box.input_value()
    report.check(
        "あなたの言葉として送られます" in family_row.inner_text(), "直した文の説明が出ていない"
    )
    edited_box.fill("薬を持ってきてください")
    page.get_by_role("button", name="この案で進める").click()
    page.get_by_text("医療の語が入っている文は送れません").wait_for()
    edited_box.fill(original_draft + "土曜でもいいよ。")
    family_row.get_by_role("button", name="この文にする").click()
    inspect(page, "18c-decide-edited", out, report)
    pressed = request("GET", "/api/board", code)
    report.check(pressed["branches"] == [], "押す前に案が台帳に入った")
    page.get_by_role("button", name="この案で進める").click()
    # 次にやること：進めたら開く。データのある線だけ。カードには、見た人がすることを書く。
    page.get_by_text("木曜の退院候補を取り消し").wait_for()
    page.wait_for_timeout(2500)  # 線が灯り終わるまで
    inspect(page, "18a-map", out, report)
    report.check(page.get_by_text("連絡が2件あります").count() == 1, "連絡の件数が出ていない")
    effects = {
        node.get_attribute("data-part"): node.get_attribute("data-effects")
        for node in page.locator(".map-node").all()
    }
    report.check(
        effects
        == {
            "家族": "変える",
            "職場": "そのまま",  # 職場の案は外したので、線は「そのまま」
            "保育園・預け先": "外す,変える",
            "病院": "そのまま",
        },
        f"地図の線が台帳と違う: {effects}",
    )
    report.check(list(effects)[0] == "家族", f"カードの順が違う: {list(effects)}")
    report.check(page.locator(".map-line").count() == 5, "線の本数が台帳と違う")
    report.check("支援" not in effects, "データの無い線が描かれている")
    family_node = page.locator(".map-node[data-part='家族']").inner_text()
    report.check(
        "夫に頼む" in family_node and "未依頼" in family_node, f"やることが無い: {family_node}"
    )
    childcare_node = page.locator(".map-node[data-part='保育園・預け先']").inner_text()
    report.check(
        "前の確認を取り消し" in childcare_node and "保育園に空きを確認する" in childcare_node,
        f"やることが無い: {childcare_node}",
    )
    report.check(
        "変更なし" in page.locator(".map-node[data-part='病院']").inner_text(), "変更なしが無い"
    )
    strip = page.locator(".week-strip").inner_text()
    report.check("取消" in strip and "候補" in strip, f"帯に候補日が無い: {strip}")
    report.check(
        request("GET", "/api/board", code)["impact"]["headline"] == "木曜の退院候補を取り消し",
        "地図の見出しが台帳と違う",
    )
    page.get_by_text("なぜ変わったか").click()
    page.get_by_text("今日の採血はだめでした").first.wait_for()

    # カードを開く：その場で送れる。取り消す確認と、根拠の原文も見える。
    page.locator(".map-node[data-part='保育園・預け先']").click()
    page.get_by_role("heading", name="取り消す確認").wait_for()
    detail = page.locator("main").inner_text()
    page.get_by_text("根拠を見る").click()
    detail = page.locator("main").inner_text()
    for text in ("取り消す確認", "木の延長の空きを確認", "火曜の延長保育",
                 "根拠", "今日の採血はだめでした", "コピー"):  # fmt: skip
        report.check(text in detail, f"カードに「{text}」が無い")
    inspect(page, "18b-map-card", out, report)
    page.get_by_role("button", name="戻る").click()
    page.locator(".map-node[data-part='病院']").click()
    page.get_by_role("heading", name="そのままのこと").wait_for()
    hospital = next(
        node
        for node in request("GET", "/api/board", code)["impact"]["nodes"]
        if node["part"] == "病院"
    )
    report.check(hospital["kept"] != [], "病院に、そのままの用件が無い")
    shown_text = page.locator("main").inner_text()
    for entry in hospital["kept"]:
        report.check(entry["text"] in shown_text, f"そのままの用件が出ていない: {entry['text']}")
    page.get_by_role("button", name="戻る").click()

    # ［連絡を送る］は最初の未送信の相手を開く。
    page.get_by_role("button", name="連絡を送る（2件）").click()
    page.get_by_role("heading", name="夫に頼む").wait_for()
    page.get_by_role("button", name="戻る").click()
    page.get_by_role("button", name="ボードへ戻る").click()
    open_sends(page)
    inspect(page, "19-board-after", out, report)
    report.check(
        page.get_by_role("button", name="次にやることを見る").count() == 1, "地図の入口が無い"
    )
    after = request("GET", "/api/board", code)
    report.check(len(after["branches"]) == 1, "案が台帳に入っていない")
    kept = after["items"][: len(pressed["items"])]
    report.check(kept == pressed["items"], "進めただけで今ある項目が変わった")
    report.check(page.locator("[data-send='職場']").count() == 0, "外した案が残っている")
    report.check(page.locator("[data-send='保育園・預け先']").count() == 1, "選んだ案が無い")
    return code


def round_trip(browser, page: Page, code: str, out: Path, report: Report) -> None:
    """関門2：頼む → 夫が持っていく → 開いたままの母の画面に戻る。"""
    family_card = page.locator("[data-send='家族']")
    childcare_card = page.locator("[data-send='保育園・預け先']")
    report.check(
        family_card.get_by_role("button", name="共有").count() == 0, "使えない共有が出ている"
    )
    report.check(
        family_card.get_by_role("button", name="頼んだにする").count() == 0,
        "送る前に［頼んだにする］が出ている",
    )

    # コピー：保育園の文には、ボードの鍵を入れない。押しても状態は自動で変えない。
    page.context.grant_permissions(["clipboard-read", "clipboard-write"], origin=BASE)
    childcare_card.get_by_role("button", name="コピー").click()
    childcare_card.get_by_text("コピーしました").wait_for()
    copied = page.evaluate("navigator.clipboard.readText()")
    report.check("火曜の延長" in copied, "コピーした文が違う")
    report.check("/m#" not in copied and code not in copied, "保育園の文にボードの鍵が入った")
    childcare_id = childcare_card.get_attribute("data-item")
    childcare = next(
        i for i in request("GET", "/api/board", code)["items"] if i["id"] == childcare_id
    )
    report.check(childcare["status"] == "頼みたい", "コピーしただけで状態が変わった")
    inspect(page, "21-send", out, report)
    childcare_card.get_by_role("button", name="頼んだにする").click()
    moved = page.locator(f"main section:not(:has(.send-actions)) [data-item='{childcare_id}']")
    moved.get_by_text("返答待ち").wait_for()

    # 共有：使える端末では大きく出て、家族の文にはリンクが付く。
    page.add_init_script("navigator.share = async (data) => { window.__shared = data }")
    page.reload(wait_until="networkidle")
    open_sends(page)
    family_card.get_by_role("button", name="共有").click()
    family_card.get_by_role("button", name="頼んだにする").wait_for()
    text = page.evaluate("window.__shared.text")
    report.check("着替えを追加" in text, "共有した文に用件が無い")
    link = re.search(r"http\S+/m#\S+", text)
    report.check(link is not None, "共有した文に1件のリンクが無い")
    item_id = family_card.get_attribute("data-item")
    # LINEから戻ると開き直されることがある。［頼んだにする］は残る。
    page.reload(wait_until="networkidle")
    open_sends(page)
    family_card.get_by_role("button", name="頼んだにする").click()
    home_card = page.locator(f"[data-item='{item_id}']")
    home_card.get_by_text("返答待ち").wait_for()

    # 夫の端末。アプリもボードの閲覧の履歴も無い。
    husband = browser.new_context(
        viewport={"width": WIDTH, "height": HEIGHT},
        device_scale_factor=2,
        color_scheme="light",
        has_touch=True,
    )
    h_page = husband.new_page()
    h_page.goto(link[0], wait_until="networkidle")
    h_page.get_by_role("button", name="持っていく").wait_for()
    report.check(code not in h_page.evaluate("location.pathname"), "家族コードがパスに入っている")
    # 押す前は4つとも同じ見た目（1つだけ塗ると、選択済みに見える）。
    looks = h_page.locator("button.answer").evaluate_all(
        "els => els.map((el) => { const s = getComputedStyle(el); "
        "return [s.backgroundColor, s.color, s.borderColor, el.getBoundingClientRect().height]; })"
    )
    report.check(len(looks) == 4, f"家族の選択が{len(looks)}個")
    report.check(all(look == looks[0] for look in looks), f"押す前の見た目が違う: {looks}")
    report.check(looks[0][3] >= 56, f"家族の選択の高さ {looks[0][3]}px")
    fixed_note(h_page, "家族の1件", report)
    body = h_page.locator("body").inner_text()
    report.check("着替えを追加" in body, "1件の画面に用件が無い")
    report.check("寝室のタンスの2段目" in body, "1件の画面にありかが無い")
    inspect(h_page, "22-one", out, report)
    report.check(
        h_page.evaluate("localStorage.getItem('ensemble.lastCode')") is None,
        "1件を開いただけで、この端末のボードが変わった",
    )

    # 押すと、母は開き直さなくても5秒以内に見える（これが関門2）。
    h_page.get_by_role("button", name="持っていく").click()
    h_page.get_by_text("「引き受けた」と伝えました").wait_for()
    inspect(h_page, "23-one-answered", out, report)
    home_card.get_by_text("引き受けた（夫）").wait_for(timeout=5000)
    report.check("新" in home_card.inner_text(), "母の画面に「新」が付かない")
    # 閉じていても、タイルだけで返事の中身と「新」が読める。
    family_tile = page.locator("[data-tile='家族']").inner_text()
    report.check(
        "引き受けた（夫）" in family_tile and "新" in family_tile,
        f"タイルに返事が無い: {family_tile}",
    )
    inspect(page, "24-board-returned", out, report)

    # ◯時なら：答えを変えられ、時刻は選ぶだけ。
    h_page.get_by_role("button", name="答えを変える").click()
    h_page.get_by_role("button", name="◯時なら").click()
    inspect(h_page, "25-one-time", out, report)
    h_page.get_by_role("button", name="明日の朝").click()
    h_page.get_by_text("「明日の朝なら」と伝えました").wait_for()
    home_card.get_by_text("明日の朝なら（夫）").wait_for(timeout=5000)

    # 暗い画面の1件。
    dark = browser.new_context(
        viewport={"width": WIDTH, "height": HEIGHT},
        device_scale_factor=2,
        color_scheme="dark",
        has_touch=True,
    )
    dark_page = dark.new_page()
    dark_page.goto(link[0], wait_until="networkidle")
    dark_page.get_by_role("button", name="答えを変える").click()
    inspect(dark_page, "26-one-dark", out, report)
    dark.close()

    # 消えた項目のリンク。
    h_page.goto(f"{BASE}/m#{code}/nothing")
    h_page.reload(wait_until="networkidle")
    h_page.get_by_text("この用事は見つかりません").wait_for()
    inspect(h_page, "27-one-missing", out, report)
    husband.close()

    # 返事が来た：［◯時まで］で記録する。ほかの返答待ちは残る。AIは使わない。
    moved = page.locator(f"[data-item='{childcare_id}']")
    moved.get_by_role("button", name="返事が来た").click()
    moved.get_by_role("button", name="◯時まで").wait_for()
    inspect(page, "28-reply-open", out, report)
    moved.get_by_role("button", name="◯時まで").click()
    moved.locator("input[type=time]").fill("18:30")
    inspect(page, "29-reply-time", out, report)
    moved.get_by_role("button", name="記録する").click()
    moved.get_by_text("18:30まで可").wait_for()
    report.check(
        moved.get_by_role("button", name="返事が来た").count() == 0, "答えの後も返事のボタンが残る"
    )
    other = page.locator("[data-item='a-05']")
    report.check("返答待ち" in other.inner_text(), "ほかの返答待ちが変わった")
    inspect(page, "30-board-replied", out, report)


def withdraw_basis(page: Page, code: str, out: Path, report: Report) -> None:
    """案の根拠の項目も消せて、取り下げを先に知らせる。ゴミ箱から案ごと戻る。"""
    before = request("GET", "/api/board", code)
    unsent_family = [
        i["id"]
        for i in before["items"]
        if i["id"].startswith(f"s-{before['branches'][-1]['id']}") and i["part"] == "保育園・預け先"
    ]
    basis = page.locator("[data-item='a-02']")
    basis.scroll_into_view_if_needed()
    basis.get_by_role("button", name="消す").click()
    note = page.get_by_text("これを根拠にした案も取り下げます")
    note.wait_for()
    box = note.bounding_box()
    report.check(0 <= box["y"] < HEIGHT, f"確認が画面の外にある（{box['y']}）")
    inspect(page, "31-board-withdraw", out, report)
    page.locator(".dock-note.confirm").get_by_role("button", name="消す", exact=True).click()
    basis.wait_for(state="detached")
    page.get_by_text("ゴミ箱に入れました").wait_for()
    after = request("GET", "/api/board", code)
    ids = {i["id"] for i in after["items"]}
    report.check("a-02" not in ids, "根拠の項目が消えていない")
    report.check(set(unsent_family) <= ids, "別の案の連絡まで消えた")
    latest = after["branches"][-1]["outcomes"][0]["actions"]
    report.check(
        any(action["part"] == "家族" and action["type"] == "何もしない" for action in latest),
        "根拠にした案が取り下がっていない",
    )

    # ゴミ箱から戻すと、取り下げた案も戻る。
    page.get_by_role("button", name="ゴミ箱を開く").click()
    trashed = page.locator("[data-trash='a-02']")
    trashed.get_by_text("取り下げた案も戻ります").wait_for()
    trashed.get_by_role("button", name="元に戻す").click()
    page.get_by_text("元に戻しました").wait_for()
    back = request("GET", "/api/board", code)
    report.check("a-02" in {i["id"] for i in back["items"]}, "戻しても項目が無い")
    report.check(
        back["branches"][-1]["outcomes"][0]["actions"]
        == before["branches"][-1]["outcomes"][0]["actions"],
        "戻しても案が戻らない",
    )
    page.get_by_role("button", name="戻る").click()
    page.locator("[data-tile='職場']").wait_for()


def photo_store(browser, page: Page, code: str, out: Path, report: Report) -> None:
    """写真はこの端末の中だけ。サーバーにもAIにも送らない。"""
    picture = {"name": "test.png", "mimeType": "image/png", "buffer": synthetic_png()}
    sent: list = []

    def watch(request_) -> None:
        sent.append(request_)

    page.on("request", watch)

    # 写真だけ：AIを呼ばず、保管庫に入る。
    page.get_by_role("button", name="＋情報を追加する").click()
    submit = page.get_by_role("button", name="追加する", exact=True)
    report.check(submit.is_disabled(), "文も写真も無いのに追加できる")
    page.locator("input[type=file]").set_input_files([picture, picture])
    page.get_by_text("この端末の中だけに保存します").wait_for()
    report.check(page.locator(".photo-chosen").count() == 2, "選んだ写真の数が違う")
    inspect(page, "33-add-photo", out, report)
    page.get_by_role("button", name="2枚目の写真を外す").click()
    report.check(page.locator(".photo-chosen").count() == 1, "写真を外せない")
    page.get_by_role("button", name="検査結果", exact=True).click()
    submit.click()
    page.get_by_text("写真を保管庫に入れました").wait_for()
    report.check(not any("/intake" in r.url for r in sent), "写真だけなのに仕分けを呼んだ")

    # 写真と文：文だけをAIへ送り、仕分けた項目に写真が付く。
    page.get_by_role("button", name="＋情報を追加する").click()
    page.get_by_role("textbox").fill(SORTED_TEXT)
    page.locator("input[type=file]").set_input_files([picture])
    page.get_by_role("button", name="追加する", exact=True).click()
    card = page.locator("[data-item]", has_text=SORTED_TEXT).last
    card.wait_for()
    card.locator(".photo-thumb").wait_for()
    inspect(page, "34-board-photo", out, report)
    card.locator(".photo-thumb").click()
    page.get_by_text("一緒に入れた文").wait_for()
    report.check(SORTED_TEXT in page.locator("main").inner_text(), "写真の画面に文が出ない")
    inspect(page, "35-photo-viewer", out, report)
    page.get_by_role("button", name="戻る").click()

    # 保管庫：履歴と、種類ごとの棚。
    page.get_by_role("button", name="保管庫を開く").click()
    tiles = page.locator(".photo-tile")
    tiles.first.wait_for()
    report.check(tiles.count() == 2, f"保管庫の写真が{tiles.count()}枚")
    inspect(page, "36-vault", out, report)
    page.get_by_role("button", name="検査結果", exact=True).click()
    report.check(tiles.count() == 1, "検査結果の棚の枚数が違う")
    page.get_by_role("button", name="入院のしおり", exact=True).click()
    page.get_by_text("まだ写真はありません").wait_for()
    report.check(tiles.count() == 0, "空の棚に写真が出る")
    page.get_by_role("button", name="すべて").click()
    tiles.first.wait_for()

    # 種類を変えられて、開き直しても写真は残る。
    tiles.first.click()
    page.get_by_role("button", name="病院の資料", exact=True).click()
    page.get_by_role("button", name="戻る").click()
    page.reload(wait_until="networkidle")
    tiles.first.wait_for()
    page.get_by_role("button", name="病院の資料", exact=True).click()
    report.check(tiles.count() == 1, "種類の変更が残らない")
    page.get_by_role("button", name="すべて").click()

    # 消す前に聞く。消した写真はゴミ箱から戻せて、完全に消すこともできる。
    tiles.first.click()
    page.get_by_role("button", name="消す").click()
    page.get_by_role("button", name="もう一度押すと、ゴミ箱に入れます").click()
    tiles.first.wait_for()
    report.check(tiles.count() == 1, "写真を消せない")
    page.get_by_role("button", name="戻る").click()
    page.get_by_role("button", name="ゴミ箱を開く").click()
    deleted_photo = page.locator("[data-trash-photo]")
    deleted_photo.wait_for()
    inspect(page, "36a-trash-photo", out, report)
    deleted_photo.get_by_role("button", name="元に戻す").click()
    page.get_by_text("元に戻しました").wait_for()
    page.get_by_role("button", name="戻る").click()
    page.get_by_role("button", name="保管庫を開く").click()
    tiles.first.wait_for()
    report.check(tiles.count() == 2, "戻した写真が保管庫に無い")
    tiles.first.click()
    page.get_by_role("button", name="消す").click()
    page.get_by_role("button", name="もう一度押すと、ゴミ箱に入れます").click()
    tiles.first.wait_for()
    page.get_by_role("button", name="戻る").click()
    page.get_by_role("button", name="ゴミ箱を開く").click()
    deleted_photo.get_by_role("button", name="完全に消す").click()
    deleted_photo.get_by_role("button", name="もう一度押すと、戻せなくなります").click()
    page.get_by_text("完全に消しました").wait_for()
    report.check(deleted_photo.count() == 0, "写真を完全に消せない")
    page.get_by_role("button", name="戻る").click()
    page.remove_listener("request", watch)

    # 外へは何も送っていない：どの通信にも画像が無い。
    for request_ in sent:
        body = request_.post_data_buffer or b""
        kind = request_.headers.get("content-type", "")
        report.check(len(body) < 4000, f"大きな通信がある: {request_.url} {len(body)}")
        report.check("image" not in kind and "multipart" not in kind, f"画像の通信: {request_.url}")
        report.check(
            b"data:image" not in body and b"PNG" not in body, f"画像の中身: {request_.url}"
        )
    report.check(any("/intake" in r.url for r in sent), "文の仕分けが呼ばれていない")

    # 写真を保存できないブラウザでも、文は追加でき、ボードは動く。
    blocked = browser.new_context(viewport={"width": WIDTH, "height": HEIGHT}, has_touch=True)
    blocked.add_init_script(
        "Object.defineProperty(window, 'indexedDB', {get() { throw new Error('blocked') }})"
    )
    b_page = blocked.new_page()
    b_page.goto(f"{BASE}/b#{code}", wait_until="networkidle")
    b_page.evaluate("localStorage.setItem('ensemble.consented', 'true')")
    b_page.reload(wait_until="networkidle")
    b_page.locator("[data-tile='職場']").wait_for()
    b_page.get_by_role("button", name="＋情報を追加する").click()
    b_page.locator("input[type=file]").set_input_files([picture])
    b_page.get_by_role("button", name="追加する", exact=True).click()
    b_page.get_by_text("このブラウザでは写真を保存できません").wait_for()
    inspect(b_page, "37-add-photo-blocked", out, report)
    b_page.get_by_role("button", name="1枚目の写真を外す").click()
    b_page.get_by_role("textbox").fill("売店で買う")
    b_page.get_by_role("button", name="追加する", exact=True).click()
    b_page.locator("[data-tile='職場']").wait_for()
    b_page.get_by_role("button", name="保管庫を開く").click()
    b_page.get_by_text("このブラウザでは写真を保存できません").wait_for()
    blocked.close()


def reading_flow(page: Page, code: str, out: Path, report: Report) -> None:
    """資料だけを、親が選んだときだけ読ませる。質問には資料の文の引用で答える。"""
    picture = {"name": "shiori.png", "mimeType": "image/png", "buffer": synthetic_png()}
    sent: list = []

    def watch(request_) -> None:
        sent.append(request_)

    def reads() -> list:
        return [r for r in sent if "/api/photo/read" in r.url]

    def searches() -> list:
        return [r for r in sent if r.url.endswith("/search")]

    def ask(text: str) -> None:
        page.get_by_role("button", name="＋情報を追加する").click()
        page.get_by_role("button", name="聞きたい").click()
        page.get_by_role("button", name="医師・看護師").click()
        page.get_by_role("textbox").fill(text)
        page.get_by_role("button", name="追加する", exact=True).click()

    page.on("request", watch)

    # 検査の結果には、読ませる選択が出ない。選ばなければ何も送らない。
    page.get_by_role("button", name="＋情報を追加する").click()
    page.locator("input[type=file]").set_input_files([picture])
    toggle = page.get_by_role("button", name="AIに読ませて、後で探せるようにする")
    report.check(toggle.count() == 0, "種類を選ぶ前に読ませる選択が出ている")
    page.get_by_role("button", name="検査結果", exact=True).click()
    report.check(toggle.count() == 0, "検査結果に読ませる選択が出ている")
    page.get_by_role("button", name="その他", exact=True).click()
    report.check(toggle.count() == 0, "その他に読ませる選択が出ている")
    page.get_by_role("button", name="入院のしおり", exact=True).click()
    toggle.wait_for()
    report.check(
        toggle.get_attribute("aria-pressed") == "false", "初めから読ませる設定になっている"
    )
    inspect(page, "38-add-let-read", out, report)
    page.get_by_role("button", name="追加する", exact=True).click()
    page.get_by_text("写真を保管庫に入れました").wait_for()
    report.check(reads() == [], "選んでいないのに写真を送った")

    # 読ませると選んだ写真だけを送る。
    page.get_by_role("button", name="＋情報を追加する").click()
    page.locator("input[type=file]").set_input_files([picture])
    page.get_by_role("button", name="入院のしおり", exact=True).click()
    toggle.click()
    page.get_by_text("名前が写っていないか確かめてください").wait_for()
    inspect(page, "39-add-let-read-on", out, report)
    note_gap = page.get_by_text("名前が写っていないか確かめてください").evaluate(
        "el => document.querySelector('.dock').getBoundingClientRect().top"
        " - el.getBoundingClientRect().bottom"
    )
    report.check(note_gap >= 0, f"AIへ送る注意書きが帯に隠れる（{note_gap}px）")
    page.get_by_role("button", name="追加する", exact=True).click()
    page.get_by_text("資料を読み取りました").wait_for()
    report.check(len(reads()) == 1, f"読み取りが{len(reads())}回")
    body = json.loads(reads()[0].post_data)
    report.check(body["category"] == "入院のしおり", "読み取りの種類が違い")
    report.check(body["media_type"] == "image/jpeg", "縮めた写真を送っていない")

    # 質問すると、資料の文の引用が出る。元の写真を開ける。
    ask("スリッパって使っていいんだっけ")
    question = page.locator("[data-item]", has_text="スリッパって使っていいんだっけ").first
    question.get_by_text("入院のしおりにありました").wait_for()
    report.check("スリッパはお控えください。" in question.inner_text(), "引用が出ない")
    inspect(page, "40-board-quote", out, report)
    question.get_by_role("button", name="写真を見る").click()
    page.get_by_text("読み取った文").wait_for()
    inspect(page, "41-viewer-read", out, report)
    page.get_by_text("読み取った文").click()
    report.check(
        "スリッパはお控えください" in page.locator("main").inner_text(), "読み取った文が無い"
    )
    page.get_by_role("button", name="戻る").click()

    # 資料に無ければ、無いと言う。
    ask("Wi-Fiは使える？")
    other = page.locator("[data-item]", has_text="Wi-Fiは使える？").first
    other.get_by_text("保管庫には見つかりませんでした").wait_for()
    inspect(page, "42-board-not-found", out, report)

    # 医療の問いは探さず、固定文だけ。
    before = len(searches())
    add_text(page, "薬を増やしたほうがいいですか")
    asked = page.locator("[data-item]", has_text="薬を増やしたほうがいいですか").last
    asked.wait_for()
    page.wait_for_timeout(2500)
    report.check(MEDICAL_FIXED_TEXT in asked.inner_text(), "医療の問いに固定文が出ない")
    report.check(len(searches()) == before, "医療の問いで保管庫を探した")
    report.check(
        asked.get_by_role("button", name="保管庫から探す").count() == 0, "探すボタンが出ている"
    )

    # 資料より前に入れた質問は、押して探せる。
    version = request("GET", "/api/board", code)["version"]
    request(
        "POST",
        "/api/board/items",
        code,
        {
            "version": version,
            "kind": "質問",
            "part": "病院",
            "certainty": "見込み",
            "text_original": "スリッパはどこで買える",
            "speaker": "親",
            "status": "未確認",
        },
    )
    page.reload(wait_until="networkidle")
    early = page.locator("[data-item]", has_text="スリッパはどこで買える").first
    early.get_by_role("button", name="保管庫から探す").click()
    early.get_by_text("入院のしおりにありました").wait_for()

    # 検査結果へ変えたら、読み取った文を消して、探す対象から外す。
    page.get_by_role("button", name="保管庫を開く").click()
    tile = page.locator(".photo-tile", has_text="入院のしおり").first
    tile.wait_for()
    tile.click()
    # 読み取った方の写真を開くまで順に見る。
    if page.get_by_text("読み取った文").count() == 0:
        page.get_by_role("button", name="戻る").click()
        page.locator(".photo-tile", has_text="入院のしおり").nth(1).click()
    page.get_by_text("読み取った文").wait_for()
    page.get_by_role("button", name="検査結果", exact=True).click()
    page.get_by_text("読み取った文").wait_for(state="detached")
    page.get_by_role("button", name="戻る").click()
    page.get_by_role("button", name="戻る").click()
    page.locator("[data-tile='職場']").wait_for()
    page.wait_for_timeout(500)
    report.check(
        page.get_by_role("button", name="保管庫から探す").count() == 0,
        "読み取った資料が無いのに探すボタンが出ている",
    )

    # あとから読ませることもできる。
    page.get_by_role("button", name="保管庫を開く").click()
    page.locator(".photo-tile", has_text="入院のしおり").first.click()
    page.get_by_role("button", name="AIに読ませる", exact=True).click()
    page.get_by_text("読み取った文").wait_for()
    report.check(len(reads()) == 2, f"読み取りが{len(reads())}回")
    page.get_by_role("button", name="戻る").click()
    page.get_by_role("button", name="戻る").click()
    page.remove_listener("request", watch)

    # 写真の中身が送られたのは、読み取りの要求だけ。
    for request_ in sent:
        if "/api/photo/read" in request_.url:
            continue
        data = request_.post_data_buffer or b""
        report.check(len(data) < 8000, f"大きな通信がある: {request_.url} {len(data)}")
        report.check(b"data:image" not in data, f"画像の中身: {request_.url}")


def remove_board(browser, page: Page, out: Path, report: Report) -> str:
    """このボードを消す。ほかの端末も見つからない表示になり、端末にも残らない。"""
    code = page.evaluate("location.hash.slice(1)")
    # 何も消していなければ、ゴミ箱は空。
    page.get_by_role("button", name="ゴミ箱を開く").click()
    page.get_by_text("ゴミ箱は空です").wait_for()
    inspect(page, "31a-trash-empty", out, report)
    page.get_by_role("button", name="戻る").click()
    # 写真もボードと一緒に消える。
    picture = {"name": "test.png", "mimeType": "image/png", "buffer": synthetic_png()}
    page.evaluate("localStorage.setItem('ensemble.consented', 'true')")
    page.reload(wait_until="networkidle")
    page.get_by_role("button", name="＋情報を追加する").click()
    page.locator("input[type=file]").set_input_files([picture])
    page.get_by_role("button", name="追加する", exact=True).click()
    page.get_by_text("写真を保管庫に入れました").wait_for()
    count_photos = """(code) => new Promise((resolve, reject) => {
      const opening = indexedDB.open('ensemble-photos', 1);
      opening.onerror = () => reject(opening.error);
      opening.onsuccess = () => {
        const req = opening.result.transaction('photos').objectStore('photos')
          .index('code').count(code);
        req.onsuccess = () => { opening.result.close(); resolve(req.result); };
        req.onerror = () => reject(req.error);
      };
    })"""
    report.check(page.evaluate(count_photos, code) == 1, "写真が端末に保存されていない")
    other = browser.new_context(viewport={"width": WIDTH, "height": HEIGHT})
    other_page = other.new_page()
    other_page.goto(f"{BASE}/b#{code}", wait_until="networkidle")
    other_page.locator("[data-tile='支援']").wait_for()

    page.get_by_role("button", name="同意の内容を読む").click()
    page.get_by_role("button", name="このボードを消す").wait_for()
    inspect(page, "32-consent-remove", out, report)
    page.get_by_role("button", name="このボードを消す").click()
    report.check(request("GET", "/api/board", code)["version"] >= 1, "1回押しで消えた")
    page.get_by_role("button", name="もう一度押すと消します").click()
    page.get_by_text("試す家庭を選ぶ").wait_for()
    try:
        request("GET", "/api/board", code)
        report.check(False, "消した後も読める")
    except urllib.error.HTTPError as error:
        report.check(error.code == 404, f"消した後の応答が {error.code}")
    keys = page.evaluate("Object.keys(localStorage).join(' ')")
    report.check(code not in keys, "消したボードの控えが端末に残っている")
    page.wait_for_timeout(500)
    report.check(page.evaluate(count_photos, code) == 0, "消したボードの写真が端末に残っている")
    other_page.get_by_text("ボードが見つかりません").wait_for(timeout=5000)
    other.close()
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
        env = {
            **os.environ,
            "DATABASE_URL": f"sqlite:///{(Path(tmp) / 'check.db').as_posix()}",
            # Windowsの文字コードで出たエラー文で、記録の読み取りが落ちないように。
            "PYTHONUTF8": "1",
            # 偽のAIなので費用はかからない。回数の上限はテストで確かめる。
            "AI_FAMILY_DAILY_LIMIT": "100000",
            "AI_DAILY_LIMIT": "100000",
        }
        with log_path.open("w", encoding="utf-8") as log:
            server = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "fake_ai_app:app", "--app-dir", "scripts",
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
                    round_trip(browser, page, code, args.out, report)
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
                    dark_page.locator("[data-tile='職場']").wait_for()
                    inspect(dark_page, "10-board-dark", args.out, report)
                    dark_page.evaluate("localStorage.setItem('ensemble.consented', 'false')")
                    dark_page.get_by_role("button", name="＋情報を追加する").click()
                    inspect(dark_page, "11-consent-dark", args.out, report)
                    dark_page.get_by_role("button", name="わかった").click()
                    inspect(dark_page, "12-add-dark", args.out, report)
                    dark_page.get_by_role("button", name="戻る").click()
                    add_text(dark_page, "今日の採血はだめでした")
                    dark_page.get_by_role("button", name="確かめる").click()
                    dark_page.get_by_role("button", name="この案で進める").wait_for()
                    inspect(dark_page, "20-decide-dark", args.out, report)
                    # 案を進めて、その場で順に送る。送ったら次の相手へ進み、件数が減る。
                    dark.grant_permissions(["clipboard-read", "clipboard-write"], origin=BASE)
                    dark_page.get_by_role("button", name="この案で進める").click()
                    dark_page.locator(".map-node").first.wait_for()
                    dark_page.get_by_text("連絡が3件あります").wait_for()
                    dark_page.wait_for_timeout(2500)
                    inspect(dark_page, "20a-map-dark", args.out, report)
                    dark_page.get_by_role("button", name="連絡を送る（3件）").click()
                    dark_page.get_by_role("heading", name="夫に頼む").wait_for()
                    dark_page.wait_for_timeout(1800)
                    inspect(dark_page, "20b-map-card-dark", args.out, report)
                    card_text = dark_page.locator("main").inner_text()
                    report.check("取り消す確認" not in card_text, "取り消していないのに出ている")
                    report.check(
                        dark_page.locator("main").text_content().count("今日の採血はだめでした")
                        == 1,
                        "根拠が重なっている",
                    )
                    dark_page.get_by_role("button", name="コピー").click()
                    dark_page.get_by_role("button", name="頼んだにする").click()
                    dark_page.get_by_role("heading", name="職場に伝える").wait_for()
                    dark_page.get_by_role("button", name="戻る").click()
                    dark_page.get_by_text("連絡が2件あります").wait_for()
                    report.check(
                        "返答待ち" in dark_page.locator(".map-node[data-part='家族']").inner_text(),
                        "送った相手の状態が変わらない",
                    )
                    withdraw_basis(page, code, args.out, report)
                    photo_store(browser, page, code, args.out, report)
                    reading_flow(page, code, args.out, report)

                    # 消えたボードを開いたとき。
                    home_b = browser.new_context(
                        viewport={"width": WIDTH, "height": HEIGHT},
                        device_scale_factor=2,
                        color_scheme="light",
                    )
                    b_page = home_b.new_page()
                    b_page.goto(BASE + "/", wait_until="networkidle")
                    b_page.get_by_role("button", name="家庭Bで始める").click()
                    b_page.locator("[data-tile='支援']").wait_for()
                    open_parts(b_page)
                    b_page.get_by_role("heading", name="支援").wait_for()
                    report.check(
                        "18:30" in b_page.locator("body").text_content(),
                        "家庭Bの延長保育の時刻が出ていない",
                    )
                    inspect(b_page, "14-board-home-b", args.out, report)
                    removed_code = remove_board(browser, b_page, args.out, report)

                    gone = browser.new_context(viewport={"width": WIDTH, "height": HEIGHT})
                    gone_page = gone.new_page()
                    gone_page.goto(f"{BASE}/b#{'x' * 43}", wait_until="networkidle")
                    gone_page.get_by_text("ボードが見つかりません").wait_for()
                    inspect(gone_page, "13-not-found", args.out, report)
                    browser.close()
            finally:
                server.terminate()
                server.wait(timeout=10)
        server_log = log_path.read_text(encoding="utf-8", errors="replace")
    report.check("GET /api/board" in server_log, "サーバーの記録を読めていない")
    report.check(code not in server_log, "家族コードがサーバーの記録に出ている")
    report.check(removed_code not in server_log, "消したボードのコードがサーバーの記録に出ている")
    print(f"検査 {report.passes + len(report.failures)} 件／不合格 {len(report.failures)} 件")
    return 1 if report.failures else 0


if __name__ == "__main__":
    sys.exit(main())
