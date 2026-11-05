"""画面の合格の検査。ビルドして手元でサーバーを立て、幅360pxで実際に操作して確かめる。

外へは何も送らない。Claude APIも呼ばない（scripts/fake_ai_app.py の偽の応答を使う）。
DBは一時フォルダの SQLite。
使い方: python -X utf8 scripts/check_screens.py <写真の出力フォルダ> [--no-build]
"""

import argparse
import json
import os
import re
from datetime import datetime, timedelta
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
OUT_FOR_STATES = Path(tempfile.gettempdir())
# 本文は15px。小さい文字も14pxまで。
MIN_TAP, MIN_GAP, MIN_FONT, MIN_CONTRAST = 48, 8, 14, 4.5
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
            primary: el.hasAttribute('data-primary'), nowPrimary: !!el.closest('[data-next-step]'), docked: !!el.closest('.dock, .bottom-nav, .app-bar, .week-nav'), day: !!el.closest('.week-nav'),
            inView: r.top < innerHeight && r.bottom > 0,
            clear: r.bottom <= (document.querySelector('.bottom-nav')?.getBoundingClientRect().top ?? innerHeight)};
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


def inspect(page: Page, name: str, out: Path, report: Report, reach: bool = True) -> None:
    """その画面の写真を撮り、画面の約束を数値で確かめる。"""
    # 全体の写真では、下に固定したボタンが途中に写り込むので、写真の間だけ一番下へ置く。
    page.screenshot(
        path=str(out / f"{name}.png"),
        full_page=True,
        style=".dock, .bottom-nav { position: static !important; }",
    )
    data = page.evaluate(MEASURE)
    report.check(data["scrollW"] <= data["clientW"], f"{name}: 横スクロールがある")
    # 札が文字の幅より伸びていない。
    wide_chips = page.evaluate(WIDE_CHIPS)
    report.check(not wide_chips, f"{name}: 札が文字より伸びている: {wide_chips}")
    # 1つのものの操作は1行に並ぶ。
    split_rows = page.evaluate(ACTIONS_SPLIT)
    report.check(not split_rows, f"{name}: 操作が2行に分かれている: {split_rows}")
    # 入力欄が16px未満だと、iPhoneが画面を拡大する。
    small_inputs = page.evaluate(SMALL_INPUTS)
    report.check(not small_inputs, f"{name}: 入力欄の文字が16px未満: {small_inputs}")
    # 見出しの名前とボタンが、幅に収まっている。
    head_cut = page.evaluate(HEAD_CUT)
    report.check(not head_cut, f"{name}: 見出しの語が幅に収まっていない: {head_cut}")
    squeezed = page.evaluate(ROW_SQUEEZED)
    report.check(not squeezed, f"{name}: 行の文が細い列に潰れている: {squeezed}")
    # 見出しの数と、その下の行が合っている（押した先に無い、を作らない）。
    mismatch = page.evaluate(COUNTS_AND_ROWS)
    report.check(not mismatch, f"{name}: 見出しの数と行が合わない: {mismatch}")
    rects = data["rects"]
    # 主なボタンは、スクロールしなくても1つは見える（「確かめるが出ない」の再発を防ぐ）。
    primaries = [rect for rect in rects if rect["primary"]]
    report.check(
        not primaries or any(rect["inView"] for rect in primaries),
        f"{name}: 主なボタンが画面の外にしかない",
    )
    report.check(
        not primaries or any(rect["inView"] and rect["clear"] for rect in primaries),
        f"{name}: 主なボタンが下のナビに隠れる",
    )
    for rect in rects:
        report.check(rect["h"] >= MIN_TAP, f"{name}: 「{rect['text']}」の高さ {rect['h']:.0f}px")
        # 主なボタンは、開いた直後の画面の下半分に。スクロールの先にあるものは位置で測れない。
        # 「いま」は、何をするかを最初に見せるので上に置く（UX Design の判断）。親指が届くかは実機で確かめる。
        if reach and rect["primary"] and not rect["nowPrimary"] and rect["top"] < HEIGHT:
            report.check(rect["top"] >= HEIGHT / 2, f"{name}: 「{rect['text']}」が上半分にある")
    for i, a in enumerate(rects):
        for b in rects[i + 1 :]:
            # 下に固定したボタンは、スクロールの途中で本文と重なる。下の END_GAP で確かめる。
            if a["docked"] != b["docked"]:
                continue
            # 週の帯の日は8日分を幅いっぱいに並べるので、隣との間を取らない。
            if a.get("day") and b.get("day"):
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


def nav(page: Page, name: str) -> None:
    """下のナビを押す。"""
    page.locator(".bottom-nav").get_by_role("button", name=name, exact=True).click()


def to_now(page: Page) -> None:
    nav(page, "いま")
    page.locator("[data-next-step]").wait_for()


def to_parts(page: Page) -> None:
    nav(page, "やりとり")
    page.locator("[data-part='職場']").wait_for()


def tab(page: Page, name: str) -> None:
    """やりとり、進み具合、カレンダーを開く。"""
    if name == "相手別":
        to_parts(page)
    elif name == "進捗別":
        nav(page, "進み具合")
        page.locator("[data-lanes]").wait_for()
    else:
        to_now(page)
        page.locator("[data-calendar]").click()
        page.locator(".week-view").wait_for()


def plan_button(page: Page):
    """［変わるところを見る］は「いま」にある。"""
    to_now(page)
    return page.get_by_role("button", name="変わるところを見る")


def open_add(page: Page) -> None:
    to_now(page)
    page.locator("[data-now-input]").click()


def from_menu(page: Page, name: str) -> None:
    page.get_by_role("button", name="メニュー", exact=True).click()
    page.locator("[data-menu]").wait_for()
    page.get_by_role("button", name=name, exact=True).click()


def open_trash(page: Page) -> None:
    from_menu(page, "ゴミ箱")


def open_consent(page: Page) -> None:
    from_menu(page, "データと共有の設定")


def open_vault(page: Page) -> None:
    from_menu(page, "しおりから探す")


def add_text(page: Page, text: str) -> None:
    open_add(page)
    page.get_by_role("textbox").fill(text)
    page.get_by_role("button", name="整理する", exact=True).click()
    # 仕分けの文は「いま」に出て、結果の行は「やりとり」に出る。
    to_parts(page)


def no_plan(page: Page) -> bool:
    """案の入口が無いことを確かめて、やりとりへ戻る。"""
    to_now(page)
    missing = page.get_by_role("button", name="変わるところを見る").count() == 0
    to_parts(page)
    return missing


def back_from_map(page: Page) -> None:
    page.get_by_role("button", name="いまへ戻る").click()
    to_parts(page)


def close_menu(page: Page) -> None:
    page.get_by_role("button", name="メニューを閉じる").click()


def open_parts(page: Page) -> None:
    """閉じている相手の見出しを開く。開いた状態は端末に残る。"""
    closed = page.locator(".part-fold[aria-expanded='false']:enabled")
    while closed.count():
        closed.first.click()
    page.wait_for_timeout(300)
    page.evaluate("scrollTo(0, 0)")
    page.wait_for_timeout(200)


def reveal(row) -> None:
    """記録と済の畳みの中にある行を見えるようにする。"""
    row.wait_for(state="attached")
    row.evaluate("el => { const box = el.closest('details'); if (box) box.open = true }")


def open_part(page: Page, part: str) -> None:
    fold = page.locator(f"[data-part='{part}'] .part-fold")
    if fold.get_attribute("aria-expanded") == "false":
        fold.click()


def open_sends(page: Page) -> None:
    """閉じている「送る文」の行を開く。"""
    page.locator("[data-send]").first.wait_for()
    closed = page.locator("[data-send] .row-lead.send[aria-expanded='false']")
    while closed.count():
        closed.first.click()


def show_row(page: Page, item_id: str):
    """行を開く。閉じた相手や、記録と済の畳みの中にあれば、そこも開く。"""
    row = page.locator(f"[data-item='{item_id}']")
    if row.count() == 0:
        open_parts(page)
    row.evaluate("el => { const box = el.closest('details'); if (box) box.open = true }")
    closed = row.locator("button.row-main[aria-expanded='false']")
    if closed.count():
        closed.click()
    return row


def open_map(page: Page) -> None:
    """次にやることの画面へ。下書きがあるときは主なボタンから、無ければ下の行から。"""
    to_now(page)
    send = page.locator("[data-next-step]").get_by_role(
        "button", name=re.compile("下書きを見る|人分を見る")
    )
    if send.count():
        send.click()
    else:
        page.locator("[data-map-open]").click()


def within_height(page: Page, name: str, screens: float, report: Report) -> None:
    """スクロールの量が増えすぎないこと。"""
    height = page.evaluate("document.documentElement.scrollHeight")
    report.check(height <= screens * HEIGHT, f"{name}: 高さ {height}px（{screens}画面まで）")


WIDE_CHIPS = """() => [...document.querySelectorAll('.chip')]
  .filter((el) => {
    const range = document.createRange();
    range.selectNodeContents(el);
    const box = el.getBoundingClientRect();
    return box.width > 0 && box.width - range.getBoundingClientRect().width > 40;
  })
  .map((el) => el.textContent.slice(0, 12))"""

ACTIONS_SPLIT = """() => [...document.querySelectorAll('.send-actions, .plan-actions, .row-tools, .row-action, .dock-row')]
  .filter((row) => {
    const tops = [...row.children]
      .filter((el) => el.getBoundingClientRect().width > 0)
      .map((el) => el.getBoundingClientRect().top + el.getBoundingClientRect().height / 2);
    return tops.length > 1 && Math.max(...tops) - Math.min(...tops) > 2;
  })
  .map((row) => row.textContent.slice(0, 20))"""

SMALL_INPUTS = """() => [...document.querySelectorAll('textarea, input')]
  .filter((el) => el.type !== 'file' && parseFloat(getComputedStyle(el).fontSize) < 16)
  .map((el) => el.tagName)"""

HEAD_CUT = """() => [...document.querySelectorAll('.board-head, .board-head h1, .board-tools button')]
  .filter((el) => el.scrollWidth > el.clientWidth)
  .map((el) => el.textContent.slice(0, 12))"""


# 行の題が、札やボタンに押されて細い列になっていないか。
ROW_SQUEEZED = """() => [...document.querySelectorAll('.row-title')]
  .filter((el) => !el.closest('details:not([open])'))
  .filter((el) => {
    const width = el.getBoundingClientRect().width;
    return width > 0 && width < 180;
  })
  .map((el) => el.textContent.slice(0, 20))"""


# 開いている相手の、見出しの「あなた」「相手」の数と、畳みの外の行の数。
# 医療の問いは、返事があるときだけ数える。自分の一言は数えない。
COUNTS_AND_ROWS = """() => {
  const found = [];
  for (const section of document.querySelectorAll('section[data-part]')) {
    const fold = section.querySelector('.part-fold');
    if (!fold || fold.getAttribute('aria-expanded') !== 'true') continue;
    const head = section.querySelector('.part-counts').textContent;
    const counted = [...head.matchAll(/(?:これから|返事待ち|引き受けた)(\\d+)/g)].reduce((sum, m) => sum + Number(m[1]), 0);
    const rows = [...section.querySelectorAll(':scope > .rows > .row:not(.own)')]
      .filter((row) => !row.querySelector('.medical') || row.querySelector('.chip.state')).length;
    if (counted !== rows) found.push(`${section.dataset.part}: 数${counted} 行${rows}`);
  }
  return found;
}"""


LONG_SENT = "お世話になります。 木曜の延長保育に空きはありますか。 決まりしだいご連絡します。"
LONG_SENT_SHORT = "木曜の延長保育に空きはありますか"


def long_release(page: Page, code: str, out: Path, report: Report) -> None:
    """送った連絡を後の案で取り消しても、短い1行で出る。全文は「詳しく」で読める。"""
    sent = request(
        "POST",
        "/api/board/items",
        code,
        {
            "version": request("GET", "/api/board", code)["version"],
            "kind": "行動",
            "part": "保育園・預け先",
            "certainty": "見込み",
            "text_original": LONG_SENT,
            "speaker": "親",
            "status": "頼んだ",
        },
    )
    sent_id = sent["items"][-1]["id"]
    result = request(
        "POST",
        "/api/board/items",
        code,
        {
            "version": sent["version"],
            "kind": "情報",
            "part": "病院",
            "certainty": "確定",
            "text_original": "今日の採血はだめでした",
            "speaker": "親",
            "medical": "info",
        },
    )
    accepted = request(
        "POST",
        "/api/board/branches",
        code,
        {
            "version": result["version"],
            "branch": {
                "id": "x",
                "condition_item_id": "b-07",
                "candidate_days": [],
                "outcomes": [
                    {
                        "label": "採血がだめだった",
                        "actions": [
                            {
                                "part": "保育園・預け先",
                                "type": "空きを確認する",
                                "draft": "火曜の延長の空きを確認させてください",
                                "basis_ids": ["b-07", result["items"][-1]["id"]],
                                "release": [sent_id],
                            }
                        ],
                    }
                ],
            },
        },
    )

    # 次にやることのカード：全文を出さず、「→ 取り消し」が切れない。
    page.reload(wait_until="networkidle")
    open_map(page)
    node = page.locator(".map-node[data-part='保育園・預け先']")
    node.wait_for()
    node_text = node.inner_text()
    report.check(LONG_SENT_SHORT in node_text, f"前→後の行が短くない: {node_text}")
    report.check("お世話になります" not in node_text, f"前→後の行に挨拶が出た: {node_text}")
    end_cut = node.evaluate("""el => {
      const end = el.querySelector('.map-node-change-end').getBoundingClientRect();
      return end.width === 0 || end.right > el.getBoundingClientRect().right;
    }""")
    report.check(not end_cut, "「→ 取り消し」が切れている")
    inspect(page, "14b-map-long-release", out, report)

    # カードの詳細の「取り消す確認」も短い1行。
    node.click()
    page.get_by_role("heading", name="取り消す確認").wait_for()
    detail = page.locator("main").inner_text()
    report.check(LONG_SENT_SHORT in detail, "取り消す確認に短い行が無い")
    report.check("お世話になります" not in detail, "取り消す確認に全文が出た")
    page.get_by_role("button", name="戻る").click()
    back_from_map(page)

    # 取り消しの連絡を送ると、ボードの行が短くなり、文の列が潰れない。
    made = next(i for i in accepted["items"] if (i["text_original"] or "").startswith("火曜の延長"))
    request(
        "PATCH",
        f"/api/board/items/{made['id']}",
        code,
        {"version": accepted["version"], "status": "頼んだ"},
    )
    page.reload(wait_until="networkidle")
    open_parts(page)
    card = page.locator(f"[data-item='{sent_id}']")
    reveal(card)
    card.get_by_text("取り消し").wait_for()
    title = card.locator(".row-title")
    report.check(title.inner_text() == LONG_SENT_SHORT, "行の題が短くない")
    width = title.evaluate("el => el.getBoundingClientRect().width")
    report.check(width >= 200, f"取り消しの行が潰れている（幅{width:.0f}px）")
    inspect(page, "14c-board-long-release", out, report)
    # 全文は行を開くと読める。開いても順が崩れない。
    card.locator(".row-main").click()
    report.check("決まりしだいご連絡します" in card.inner_text(), "取り消した連絡の全文が読めない")
    order = card.evaluate("""el => {
      const top = (s) => el.querySelector(s).getBoundingClientRect().top;
      return top('.row-title') < top('.row-meta') && top('.row-meta') < top('.row-body');
    }""")
    report.check(order, "開いたときの行の順が崩れている")
    card.locator(".row-main").click()


OLD_MESSAGE = "水曜の夜に洗濯物を取りに来てください。木曜に帰れるかもしれません"
NEW_MESSAGE = "帰るのは木曜じゃなくて、火曜です。着替えをお願いします"


def outdated_note(browser, code: str, out: Path, report: Report) -> None:
    """取り消した日を含む前の連絡に一言添える。新しい連絡には添えず、返事は止めない。"""

    def plan_with(days: list[dict], draft: str) -> dict:
        board = request("GET", "/api/board", code)
        result = next(i for i in board["items"] if i["text_original"] == "今日の採血はだめでした")
        return request(
            "POST",
            "/api/board/branches",
            code,
            {
                "version": board["version"],
                "branch": {
                    "id": "x",
                    "condition_item_id": "b-07",
                    "candidate_days": days,
                    "outcomes": [
                        {
                            "label": "採血がだめだった",
                            "actions": [
                                {
                                    "part": "家族",
                                    "type": "頼む",
                                    "draft": draft,
                                    "basis_ids": ["b-07", result["id"]],
                                }
                            ],
                        }
                    ],
                },
            },
        )

    def day(name: str, cancelled: bool) -> dict:
        return {"day": name, "certainty": "見込み", "cancelled": cancelled}

    first = plan_with([day("木曜", False), day("火曜", False)], OLD_MESSAGE)
    old_id = first["items"][-1]["id"]
    report.check(first["items"][-1]["outdated"] is False, "取り消す前から添えている")
    # 夫の一覧に出るのは送ったものだけなので、前の連絡は送ったことにする。
    request(
        "PATCH",
        f"/api/board/items/{old_id}",
        code,
        {"version": first["version"], "status": "頼んだ"},
    )
    second = plan_with([day("木曜", True), day("火曜", False)], NEW_MESSAGE)
    new_id = second["items"][-1]["id"]

    context = browser.new_context(viewport={"width": WIDTH, "height": HEIGHT})
    page = context.new_page()
    # 新しい連絡の画面：一覧の古い連絡にだけ添える。
    page.goto(f"{BASE}/m#{code}/{new_id}", wait_until="networkidle")
    page.locator("[data-todo]").wait_for()
    report.check(page.locator("[data-outdated]").count() == 0, "新しい連絡に添えている")
    rows = page.locator("[data-todo] a")
    old_row = rows.filter(has_text="水曜の夜に洗濯物を取りに来てください")
    report.check(
        "この後に予定が変わりました" in old_row.inner_text(), "一覧の古い連絡に添えていない"
    )
    report.check(
        page.locator("[data-todo] .todo-changed").count() == 1, "関係のない連絡にも添えている"
    )
    # 文が短く一覧もあると、4択は上半分に来る。添え書きとは別の既知の点（decisions.md）。
    inspect(page, "22x-one-new-message", out, report, reach=False)
    # 古い連絡の画面：文の下に添える。返事は止めない。
    old_row.click()
    page.wait_for_function(f"location.hash.endsWith('{old_id}')")
    page.locator("[data-outdated]").wait_for()
    report.check(
        page.locator("[data-outdated]").inner_text() == "この後に予定が変わりました",
        "古い連絡の添え書きが違う",
    )
    report.check(
        page.get_by_role("button", name="持っていく").count() == 1, "古い連絡に返事ができない"
    )
    inspect(page, "22y-one-old-message", out, report, reach=False)
    context.close()

    # 取り消された前の連絡には答えられない。新しいお願いへ進める。
    board = request("GET", "/api/board", code)
    result_id = next(i for i in board["items"] if i["text_original"] == "今日の採血はだめでした")[
        "id"
    ]
    third = request(
        "POST",
        "/api/board/branches",
        code,
        {
            "version": board["version"],
            "branch": {
                "id": "x",
                "condition_item_id": "b-07",
                "candidate_days": [],
                "outcomes": [
                    {
                        "label": "採血がだめだった",
                        "actions": [
                            {
                                "part": "家族",
                                "type": "頼む",
                                "draft": "洗濯物はもう大丈夫です。火曜に着替えをお願いします",
                                "basis_ids": ["b-07", result_id],
                                "release": [old_id],
                            }
                        ],
                    }
                ],
            },
        },
    )
    replacement_id = third["items"][-1]["id"]
    request(
        "PATCH",
        f"/api/board/items/{replacement_id}",
        code,
        {"version": third["version"], "status": "頼んだ"},
    )
    context = browser.new_context(viewport={"width": WIDTH, "height": HEIGHT})
    page = context.new_page()
    page.goto(f"{BASE}/m#{code}/{old_id}", wait_until="networkidle")
    page.get_by_role("heading", name="このお願いは取り消されました").wait_for()
    report.check(
        page.get_by_role("button", name="持っていく").count() == 0, "取り消した依頼に4択が出ている"
    )
    report.check(
        page.locator("[data-outdated]").count() == 0, "取り消した依頼に添え書きが重なっている"
    )
    inspect(page, "22z-one-released", out, report, reach=False)
    try:
        request("POST", f"/api/board/items/{old_id}/reply", code, {"answer": "持っていく"})
        report.check(False, "取り消した依頼に答えられた")
    except urllib.error.HTTPError as error:
        report.check(error.code == 422, f"取り消した依頼の答えが {error.code}")
    page.get_by_role("button", name="新しいお願いを見る").click()
    page.wait_for_function(f"location.hash.endsWith('{replacement_id}')")
    page.get_by_role("button", name="持っていく").wait_for()
    report.check(
        "水曜の夜に洗濯物" not in page.locator("main").inner_text(),
        "取り消した依頼が一覧に残っている",
    )
    context.close()


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
    to_parts(page)
    code = page.evaluate("location.hash.slice(1)")
    report.check(len(code) >= 32, "家族コードがURLの # より後ろに無い")
    report.check(code not in page.evaluate("location.pathname"), "家族コードがパスに入っている")
    inspect(page, "2-board", out, report)
    # 作った直後に、URLを自分へ送る案内が出る。閉じたら開き直しても出ない。
    save_link = page.locator("[data-save-link]")
    report.check(save_link.count() == 1, "URLを送る案内が出ていない")
    save_link.get_by_role("button", name="あとで").click()
    page.reload(wait_until="networkidle")
    to_parts(page)
    report.check(save_link.count() == 0, "閉じた案内がまた出た")
    # 用件を開いたまま見せるので、畳んでいた頃の2画面より少しだけ増やす。
    within_height(page, "2-board", 2.2, report)
    # この家の事情は畳んである。開くと3枚。
    from_menu(page, "あなたとご家族のこと")
    home_cards = page.locator("[data-home-cards]")
    home_cards.locator(".card").first.wait_for()
    report.check(home_cards.locator(".card").count() == 3, "この家の事情が3枚でない")
    report.check("夫（" in home_cards.inner_text(), "用事を頼める人が出ていない")
    inspect(page, "2c-board-home", out, report)
    page.get_by_role("button", name="戻る", exact=True).click()
    page.get_by_role("button", name="メニューを閉じる").click()
    to_parts(page)
    # 初めから、用件のある相手は開いている。行の中身は押すと読める。自分のことは書くまで閉じる。
    report.check(page.locator("[data-part='職場'] .rows").count() == 1, "職場の行が見えない")
    report.check(page.locator("[data-part='親']").count() == 0, "やりとりに自分のことが出ている")
    report.check(page.locator(".row-body").count() == 0, "行が初めから開いている")
    # どの行にも時刻がある。
    times = page.locator(".row-meta time").all_text_contents()
    report.check(
        len(times) > 0 and all(re.search(r"\d+/\d+ \d+:\d\d", text) for text in times),
        f"行に時刻が無い: {times}",
    )

    body = page.locator("body").inner_text()
    for text in ("いま", "やりとり", "進み具合", "病院", "家族", "保育園・預け先", "職場",
                 "返事待ち", "引き受けた", "済"):  # fmt: skip
        report.check(text in body, f"やりとりに「{text}」が無い")
    report.check("パート" not in body, "「パート」が画面に出ている")
    work_row = page.locator("[id='part-職場'] .row").first
    work_row.locator(".row-main").click()
    report.check(
        "用件だけ残し、原文は保存していません" in work_row.inner_text(),
        "第三者の文の知らせが読めない",
    )
    report.check("左の丸を押します" in work_row.inner_text(), "今することが行に書いていない")
    inspect(page, "2d-row-open", out, report)
    work_row.locator(".row-main").click()
    report.check(
        page.locator("[data-item='a-04'] .row-lead.check").count() == 0,
        "完了の項目に丸が出ている",
    )
    report.check(
        page.locator("[data-item='a-07'] .row-lead.check").count() == 0,
        "情報の項目に丸が出ている",
    )
    columns = page.locator(".part-fold .part-head").all_inner_texts()
    report.check(
        columns == ["病院", "家族", "保育園・預け先", "職場", "支援"],
        f"列の順が違う: {columns}",
    )

    # 画面デザインの決定：色・見出し・固定文。
    to_now(page)
    inspect(page, "2e-now", out, report)
    primary_color = page.locator("[data-primary]").evaluate(
        "el => getComputedStyle(el).backgroundColor"
    )
    report.check(primary_color == "rgb(107, 74, 154)", f"主色が紫でない: {primary_color}")
    fixed_note(page, "いま", report)
    to_parts(page)
    empty_fold = page.locator("[data-part='支援'] .part-fold")
    report.check("なし" in empty_fold.inner_text(), "空の相手に「なし」が無い")
    report.check(empty_fold.is_disabled(), "空の相手の見出しが押せる")
    state_text = " ".join(page.locator(".part-counts, .row-meta").all_inner_texts())
    report.check(
        "完了" not in state_text and "未確認" not in state_text, f"状態の語が4つ以外: {state_text}"
    )
    # 見出しを閉じても、数と返事の中身は読める。閉じた状態は開き直しても残る。
    family_fold = page.locator("[data-part='家族'] .part-fold")
    family_fold.click()
    report.check(page.locator("[data-part='家族'] .rows").count() == 0, "見出しで閉じない")
    report.check("夫：引き受けた" in family_fold.inner_text(), "閉じると返事の中身が読めない")
    report.check("引き受けた1" in family_fold.inner_text(), "閉じると数が読めない")
    page.reload(wait_until="networkidle")
    report.check(family_fold.get_attribute("aria-expanded") == "false", "閉じた状態が残らない")
    family_fold.click()
    fixed_note(page, "ボード", report)

    # 進み具合：上の1行と、まとまりごとのカード。
    tab(page, "進捗別")
    report.check(
        page.locator("[data-column]").evaluate_all("els => els.map((el) => el.dataset.column)")
        == ["これから", "返事待ち", "引き受けた"],
        "進み具合の上の1行の順が違う",
    )
    inspect(page, "2a-board-progress", out, report)
    fixed_note(page, "進捗別", report)

    # 週の予定：8日の帯と日ごとの一覧。文は切らない。行を押すと相手別のその行へ。
    tab(page, "週の予定")
    inspect(page, "2b-board-week", out, report)
    days = page.locator(".week-nav [data-day]")
    report.check(days.count() == 8, f"週の帯が{days.count()}日")
    report.check(page.locator(".week-nav .today").count() == 1, "週の帯に今日の印が無い")
    week_view_box = page.locator(".week-view")
    week_text = week_view_box.inner_text()
    report.check("今日" in week_text, "一覧に今日が無い")
    report.check("明日の採血しだい" in week_text, "週の予定に医師の言葉が無い")
    report.check("木曜の会議" in week_text, "週の予定に職場の予定が無い")
    report.check(page.locator(".week-view table").count() == 0, "表が残っている")
    # 帯は1行で切れない。文も切れない。
    cut = page.evaluate("""() => [...document.querySelectorAll('.week-row-text')]
      .filter((el) => el.scrollWidth > el.clientWidth + 1
        || getComputedStyle(el).webkitLineClamp !== 'none')
      .map((el) => el.textContent.slice(0, 16))""")
    report.check(not cut, f"週の予定の文が切れている: {cut}")
    # 帯の日を押すとその日へ。
    if page.locator(".week-empty").count():
        report.check("予定なし" in week_text, "予定の無い日のまとめが無い")
    plan_row = page.locator(".week-row").first
    plan_id = plan_row.get_attribute("data-week-item")
    plan_row.click()
    opened = page.locator(f"[data-item='{plan_id}']")
    opened.wait_for()
    report.check(
        page.locator(".bottom-nav [aria-current='page']").inner_text().strip() == "やりとり",
        "週の行を押してもやりとりへ行かない",
    )
    report.check(opened.locator(".row-body").count() == 1, "週の行を押しても行が開かない")
    report.check(opened.is_visible(), "週の行を押しても行が見えない")
    opened.locator(".row-main").click()
    # 開け閉めの状態を初めの形に戻す。
    page.reload(wait_until="networkidle")
    to_parts(page)
    tab(page, "週の予定")
    undated = page.locator("[data-undated]")
    if undated.count():
        report.check("日付のない用事" in undated.inner_text(), "日付のない用事の畳みが無い")
        report.check(
            MEDICAL_FIXED_TEXT not in undated.inner_text(), "日付のない用事に医療の問いが出ている"
        )
    to_now(page)
    report.check(page.locator("[data-map-open]").count() == 0, "案が無いのに地図の入口が出ている")

    # 入れる：最初の1回だけ同意の3行。
    open_add(page)
    page.get_by_role("button", name="わかった").wait_for()
    inspect(page, "3-consent", out, report)
    page.get_by_role("button", name="わかった").click()
    box = page.get_by_role("textbox")
    inspect(page, "4-add-empty", out, report)
    report.check(
        page.get_by_role("button", name="整理する", exact=True).is_disabled(), "空で入れられる"
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
        page.get_by_role("button", name="整理する", exact=True).is_disabled(), "超えても入れられる"
    )
    report.check("あと1字" in page.locator("body").inner_text(), "超えた字数が出ていない")
    inspect(page, "5-add-over", out, report)

    # 端末に先に保存：サーバーが落ちていても残る状態を、通信を止めて確かめる。
    page.context.set_offline(True)
    page.get_by_role("textbox").fill(SORTED_TEXT)
    inspect(page, "6-add-filled", out, report)
    page.get_by_role("button", name="整理する", exact=True).click()
    # 通信が止まったままでも、入れた文と［＋入れる］は見える。読み込めていないことも正直に出す。
    page.get_by_text("仕分け中").first.wait_for()
    page.get_by_text("読み込めませんでした").wait_for()
    offline_text = page.locator("body").inner_text()
    report.check(SORTED_TEXT in offline_text, "通信なしで入れた文が見えない")
    report.check("今することはありません" not in offline_text, "読めていないのに無いと言っている")
    report.check(page.locator("[data-now-input]").count() == 1, "通信なしで入れられない")
    inspect(page, "6a-board-offline", out, report)
    page.context.set_offline(False)
    page.get_by_role("button", name="もう一度読み込む").click()
    page.locator("[data-next-step]").wait_for()
    report.check(
        "読み込めませんでした" not in page.locator("body").inner_text(), "戻っても消えない"
    )
    # つながったら仕分けが進む。途中で開き直しても文は消えず、2回入らない。
    page.get_by_text("仕分け中").first.wait_for()
    inspect(page, "7-board-sorting", out, report)
    page.reload()
    report.check(SORTED_TEXT in page.locator("body").inner_text(), "開き直すと消える")
    page.get_by_text("仕分け中").first.wait_for(state="detached")
    to_parts(page)
    reveal(page.locator("[data-item]", has_text=SORTED_TEXT).first)
    page.wait_for_timeout(2500)  # 開き直す前の呼び出しが終わるのを待つ
    page.reload(wait_until="networkidle")
    sorted_count = page.locator("[data-item]", has_text=SORTED_TEXT).count()
    report.check(sorted_count == 1, f"入れた文が{sorted_count}件になった")
    report.check("仕分け中" not in page.locator("body").inner_text(), "仕分けが終わらない")

    # もう1回押しても同意は出ない。
    open_add(page)
    report.check(page.get_by_role("textbox").count() == 1, "同意が2回出る")
    report.check(page.get_by_role("textbox").input_value() == "", "入れた後に文が残っている")
    page.get_by_role("button", name="戻る").click()

    # 「？」で同意の3行をいつでも読める。
    open_consent(page)
    page.get_by_role("button", name="閉じる").click()
    close_menu(page)
    to_parts(page)

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
    # 丸を押すと「病棟に聞いた」と残る。済の畳みに入っても固定文は読めて、数も合う。
    asked.get_by_role("button", name="病棟に聞いた").click()
    page.get_by_text("済にしました").wait_for()
    asked.locator(".row-lead.done").wait_for(state="attached")
    report.check(
        MEDICAL_FIXED_TEXT in (asked.text_content() or ""), "済にすると固定文が読めなくなる"
    )
    report.check("病棟に聞いた" in (asked.text_content() or ""), "聞いた時刻が履歴に無い")
    inspect(page, "8a2-board-medical-done", out, report)
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
    item.locator(".row-lead.check").click()
    page.get_by_text("済にしました").wait_for()
    item.locator(".row-lead.done").wait_for(state="attached")
    report.check(item.locator(".row-lead.check").count() == 0, "完了の後も丸が残る")
    report.check("新" not in (item.text_content() or ""), "自分の変更に「新」が付いた")
    inspect(page, "8b-board-done", out, report)
    # 押し間違えたら、下の帯からすぐ戻せる。戻しても時刻は残る。
    page.locator(".dock").get_by_role("button", name="元に戻す").click()
    page.get_by_text("元に戻しました").wait_for()
    item.locator(".row-lead.check").wait_for()
    events = next(i for i in request("GET", "/api/board", code)["items"] if i["id"] == "a-03")[
        "events"
    ]
    report.check([e["label"] for e in events][-2:] == ["済", "元に戻した"], f"履歴が違う: {events}")
    item.locator(".row-lead.check").click()
    page.get_by_text("済にしました").wait_for()
    item.locator(".row-lead.done").wait_for(state="attached")

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
    question.locator(".row-lead.check").click()
    page.get_by_text("もう1台で変わったので読み直しました").wait_for()
    page.unroute("**/api/board**")
    # 知らせを出してから読み直すので、読み直しが終わるのを待つ。
    page.locator("[data-item='a-06']").get_by_text("返事待ち").wait_for(timeout=5000)
    report.check(question.locator(".row-lead.check").count() == 1, "古い版の更新が通った")
    inspect(page, "9-board-conflict", out, report)
    question.locator(".row-lead.check").click()
    question.locator(".row-lead.done").wait_for(state="attached")

    # 消す：消す前に必ず聞く。やめたら何も変わらない。
    def item_ids() -> set[str]:
        return {item["id"] for item in request("GET", "/api/board", code)["items"]}

    target = show_row(page, "a-04")
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
    open_trash(page)
    trashed = page.locator("[data-trash='a-04']")
    trashed.wait_for()
    inspect(page, "9b-trash", out, report)
    trashed.get_by_role("button", name="元に戻す").click()
    page.get_by_text("元に戻しました").wait_for()
    report.check("a-04" in item_ids(), "戻しても台帳に無い")
    report.check(trashed.count() == 0, "戻した後もゴミ箱に残っている")
    page.get_by_role("button", name="戻る").click()
    target.wait_for(state="attached")
    report.check(target.locator(".row-lead.done").count() == 1, "戻した項目の状態が違う")

    # 完全に消すと戻せない（2回押し）。
    show_row(page, "a-04")
    target.get_by_role("button", name="消す").click()
    confirm.get_by_role("button", name="消す", exact=True).click()
    target.wait_for(state="detached")
    open_trash(page)
    trashed.wait_for()
    trashed.get_by_role("button", name="完全に消す").click()
    report.check(trashed.count() == 1, "1回押しで完全に消えた")
    trashed.get_by_role("button", name="もう一度押すと、戻せなくなります").click()
    page.get_by_text("完全に消しました").wait_for()
    report.check(trashed.count() == 0, "完全に消えていない")
    page.get_by_role("button", name="戻る").click()
    to_parts(page)

    add_text(page, "薬を増やしたほうがいいですか")
    asked = page.locator("[data-item]", has_text="薬を増やしたほうがいいですか").first
    asked.wait_for()
    report.check(MEDICAL_FIXED_TEXT in asked.inner_text(), "仕分けた問いに固定文が出ていない")
    report.check(no_plan(page), "医療の問いから案が作られた")

    # 第三者の文は原文を残さない。
    add_text(page, "上司：会議はどうしますか")
    boss = page.locator("[data-item]", has_text="会議の連絡").first
    reveal(boss)
    boss.locator(".row-main").click()
    report.check("原文は保存していません" in boss.text_content(), "第三者の文の知らせが無い")
    report.check("どうしますか" not in boss.text_content(), "第三者の原文が残っている")

    # 親だけの用事も画面から消えない。案にならない文でも、どこへ入ったかを知らせる。
    add_text(page, "売店で買う")
    page.get_by_text("「自分のこと」に入れました").wait_for()
    from_menu(page, "自分のこと")
    page.locator("[data-part='親'] .row").first.wait_for()
    inspect(page, "15-board-parent", out, report)

    # 親が選べること：任意で、選んだら相手の欄が出る。キーボードで狭くても押せる。
    open_add(page)
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
    submit = page.get_by_role("button", name="整理する", exact=True)
    submit.scroll_into_view_if_needed()
    box = submit.bounding_box()
    report.check(box is not None and box["y"] + box["height"] <= 420, "狭い画面で追加を押せない")
    page.set_viewport_size({"width": WIDTH, "height": HEIGHT})
    submit.click()
    to_parts(page)
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
    open_add(page)
    report.check(page.get_by_text("誰に").count() == 0, "前回の選択が残っている")
    page.get_by_role("button", name="戻る").click()

    # 仕分けを断られても、文は残り、もう一度押せる。
    add_text(page, "失敗する文")
    to_now(page)
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
    plan_button(page).wait_for(timeout=15000)
    page.unroute("**/api/board/proposal")
    report.check(calls["n"] >= 2, "送り直していない")

    # スクロールしていても［連絡の案を見る］が見える（実機で「出ない」の原因）。
    page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
    page.wait_for_timeout(200)
    box = plan_button(page).bounding_box()
    report.check(
        0 <= box["y"] < HEIGHT, f"スクロールすると［連絡の案を見る］が見えない（{box['y']}）"
    )
    page.evaluate("window.scrollTo(0, 0)")
    inspect(page, "16a-board-ready", out, report)
    plan_button(page).click()
    page.get_by_role("button", name="この案をやめる").click()
    page.get_by_role("heading", name="次にできること").wait_for()

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
    plan_button(page).wait_for()
    plan_button(page).click()
    page.get_by_role("button", name="この案をやめる").click()
    page.get_by_role("heading", name="次にできること").wait_for()
    held[0].continue_()
    page.wait_for_timeout(3000)
    page.unroute("**/api/board/proposal")
    report.check(no_plan(page), "閉じた案が戻ってきた")

    # 別のアプリへ切り替えて通信が切れても、戻れば「確かめる」まで進む。
    def back_to_app() -> None:
        page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")

    page.route("**/api/board/proposal", lambda route: route.abort())
    add_text(page, "今日の採血はだめでした")
    page.get_by_text("案を作っています").first.wait_for()
    page.wait_for_timeout(500)
    page.unroute("**/api/board/proposal")
    back_to_app()
    plan_button(page).wait_for()
    plan_button(page).click()
    page.get_by_role("button", name="この案をやめる").click()
    page.get_by_role("heading", name="次にできること").wait_for()

    # 応答が返らないままでも、戻れば進む。
    page.route("**/api/board/proposal", lambda route: None)
    add_text(page, "今日の採血はだめでした")
    page.get_by_text("案を作っています").first.wait_for()
    page.unroute("**/api/board/proposal")
    back_to_app()
    plan_button(page).wait_for()
    plan_button(page).click()
    page.get_by_role("button", name="この案をやめる").click()
    page.get_by_role("heading", name="次にできること").wait_for()

    # 仕分けの応答が届かず送り直しても、2回入らず、案まで進む。
    page.route("**/api/board/intake", lambda route: route.fetch() and route.abort())
    before_count = len(request("GET", "/api/board", code)["items"])
    add_text(page, "今日の採血はだめでした")
    page.wait_for_timeout(1500)
    page.unroute("**/api/board/intake")
    back_to_app()
    plan_button(page).wait_for()
    after_count = len(request("GET", "/api/board", code)["items"])
    report.check(after_count == before_count + 1, f"送り直しで{after_count - before_count}件入った")
    plan_button(page).click()
    page.get_by_role("button", name="この案をやめる").click()
    page.get_by_role("heading", name="次にできること").wait_for()

    # 1つの問い：［この案をやめる］は台帳を変えない。
    add_text(page, "今日の採血はだめでした")
    page.get_by_text("案を作っています").first.wait_for()
    plan_button(page).wait_for()
    before = request("GET", "/api/board", code)
    plan_button(page).click()
    page.get_by_role("button", name="下書きを作る").wait_for()
    inspect(page, "17-decide", out, report)
    fixed_note(page, "いま決めること", report)
    # 要約だけ。札は出さず、下書きの全文は送る段で読む。
    report.check(page.locator(".plan-toggle").count() == 0, "普段から切り替えが押せる")
    report.check(page.locator(".badge").count() == 0, "普段から札が出ている")
    summary_text = page.locator("main").inner_text()
    report.check("相手ごとに3件" in summary_text, "件数の要約が無い")
    report.check("夫に頼む" in summary_text, "相手ごとの用件が無い")
    report.check("会議の件" not in summary_text, "下書きの全文が出ている")
    report.check("変える" not in summary_text, "作り手の言葉が出ている")
    body = page.locator("body").inner_text()
    for text in ("今日の採血はだめでした", "よければ木曜に帰れるかも", "取り消し", "候補",
                 "こう伝え直しますか", "外す：木の延長の空きを確認"):  # fmt: skip
        report.check(text in body, f"問いの画面に「{text}」が無い")
    page.get_by_role("button", name="この案をやめる").click()
    page.get_by_role("heading", name="次にできること").wait_for()
    report.check(request("GET", "/api/board", code) == before, "何もしないで台帳が変わった")

    # 開いて戻っても案は残り、一部を外して進められる。押す前は状態が変わらない。
    add_text(page, "今日の採血はだめでした")
    plan_button(page).wait_for()
    plan_button(page).click()
    page.locator(".decide-head .back").click()
    plan_button(page).click()
    page.get_by_role("button", name="文を確かめて直す").click()
    work_row = page.locator("[data-action='職場'] .plan-toggle")
    # 選んでいる語に印が付く。塗らないので、送信のボタンに見えない。
    selected = lambda: work_row.locator(".badge.change").inner_text()
    report.check(selected().endswith("送る") and "☑" in selected(), "切り替える前の選択が違う")
    report.check(
        work_row.locator(".badge.change").evaluate(
            "el => getComputedStyle(el).backgroundColor === 'rgba(0, 0, 0, 0)'"
        ),
        "選択の札が塗られている",
    )
    work_row.click()
    report.check(
        selected().endswith("送らない") and "☑" in selected(), "押しても「送らない」にならない"
    )
    # 一部だけ変えるときは全文を読める。決める言葉に印が付く。制度の名前には付けない。
    report.check("着替えを追加" in page.locator("main").inner_text(), "全文が読めない")
    marks = page.locator("mark.decide-word").all_inner_texts()
    report.check("不要" in marks, f"決める言葉に印が付いていない: {marks}")
    report.check(
        page.locator("[data-action='保育園・預け先'] .plan-text").inner_html().count("<mark") == 3,
        "「延長保育」に印が付いている",
    )
    report.check(work_row.get_attribute("aria-pressed") == "false", "切り替えの状態が読めない")
    report.check(
        page.get_by_role("button", name="下書きを作る", exact=True).count() == 1,
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
    page.get_by_role("button", name="下書きを作る").click()
    page.get_by_text("医療の語が入っている文は送れません").wait_for()
    edited_box.fill(original_draft + "土曜でもいいよ。")
    family_row.get_by_role("button", name="この文にする").click()
    inspect(page, "18c-decide-edited", out, report)
    pressed = request("GET", "/api/board", code)
    report.check(pressed["branches"] == [], "押す前に案が台帳に入った")
    page.get_by_role("button", name="下書きを作る").click()
    # 次にやること：進めたら開く。データのある線だけ。カードには、見た人がすることを書く。
    page.get_by_text("木曜の退院候補を取り消し").wait_for()
    page.wait_for_timeout(6000)  # 線と帯が灯り終わるまで
    inspect(page, "18a-map", out, report)
    report.check(page.get_by_text("下書きが2件あります").count() == 1, "連絡の件数が出ていない")
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
        "夫に頼む" in family_node and "これから" in family_node, f"やることが無い: {family_node}"
    )
    childcare_node = page.locator(".map-node[data-part='保育園・預け先']").inner_text()
    report.check(
        "→ 取り消し" in childcare_node and "保育園に空きを確認する" in childcare_node,
        f"やることが無い: {childcare_node}",
    )
    report.check("（新規）" in family_node, f"前→後の1行が無い: {family_node}")
    # 原因は親の入力。帯はカードの上。
    report.check(
        "今日の採血はだめでした" in page.locator(".map-cause").inner_text(), "原因の行が無い"
    )
    strip_top = page.locator(".week-strip").evaluate("el => el.getBoundingClientRect().top")
    first_top = page.locator(".map-node").first.evaluate("el => el.getBoundingClientRect().top")
    report.check(strip_top < first_top, "帯がカードの上にない")
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
    page.get_by_text("根拠（").click()
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

    # ［下書きを見る］は最初の未送信の相手を開く。
    page.get_by_role("button", name="下書きを見る（2件）").click()
    page.get_by_role("heading", name="夫に頼む").wait_for()
    page.get_by_role("button", name="戻る").click()
    back_from_map(page)
    open_sends(page)
    inspect(page, "19-board-after", out, report)
    to_now(page)
    report.check(
        page.locator("[data-next-step]")
        .get_by_role("button", name=re.compile("下書きを見る|人分を見る"))
        .count()
        == 1,
        "下書きの入口が無い",
    )
    to_parts(page)
    # 未送信の連絡に丸を出さない（送らないまま済にできてしまう）。進捗別でも同じ。
    report.check(
        page.locator("[data-send] .row-lead.check").count() == 0, "未送信の連絡に丸が出ている"
    )
    tab(page, "進捗別")
    report.check(page.locator("[data-lanes] [data-send]").count() > 0, "進捗別に送る文が無い")
    report.check(
        page.locator("[data-send] .row-lead.check").count() == 0,
        "進捗別で未送信の連絡に丸が出ている",
    )
    inspect(page, "19a-progress-after", out, report)
    tab(page, "相手別")
    open_sends(page)
    after = request("GET", "/api/board", code)
    report.check(len(after["branches"]) == 1, "案が台帳に入っていない")
    kept = after["items"][: len(pressed["items"])]

    # 根拠になると原文を直せなくなるので、その表示の欄だけ比べない。
    def stored(items: list[dict]) -> list[dict]:
        return [{k: v for k, v in item.items() if k != "original_fixed"} for item in items]

    report.check(stored(kept) == stored(pressed["items"]), "進めただけで今ある項目が変わった")
    report.check(page.locator("[data-send='職場']").count() == 0, "外した案が残っている")
    report.check(page.locator("[data-send='保育園・預け先']").count() == 1, "選んだ案が無い")
    return code


def edit_and_times(page: Page, code: str, out: Path, report: Report) -> None:
    """文・相手・担当・期日を直せる。直した時刻が残る。医療の語は通さない。"""
    # 担当と期日を直すと、行にすぐ出る。
    row = show_row(page, "a-06")
    row.get_by_role("button", name="編集").click()
    panel = row.locator("[data-edit]")
    panel.wait_for()
    report.check("作った案" in panel.inner_text(), "案は変わらないことが書いていない")
    inspect(page, "20d-edit", out, report)
    panel.get_by_label("担当").fill("母")
    panel.get_by_label("期日").fill("木曜の朝")
    panel.get_by_role("button", name="保存する").click()
    page.get_by_text("直しました").wait_for()
    row.get_by_text("担当：母").wait_for()
    row.get_by_text("期日：木曜の朝").wait_for()
    report.check(panel.count() == 0, "保存しても欄が閉じない")
    report.check("直した" in row.locator(".timeline").inner_text(), "直した時刻が履歴に無い")
    # 期日は週の表にも出る（台帳の同じ欄なので）。
    saved = next(i for i in request("GET", "/api/board", code)["items"] if i["id"] == "a-06")
    report.check(saved["due"] == "木曜の朝", "期日が台帳に入っていない")
    # 開いている間にもう1台が直した欄を、触っていなければ古い文で上書きしない。
    row.get_by_role("button", name="編集").click()
    version = request("GET", "/api/board", code)["version"]
    request(
        "PATCH",
        "/api/board/items/a-06/content",
        code,
        {"version": version, "task_summary": "もう1台が直した用件"},
    )
    row.get_by_text("もう1台が直した用件").wait_for(timeout=8000)
    panel.get_by_label("期日").fill("金曜の朝")
    panel.get_by_role("button", name="保存する").click()
    row.get_by_text("期日：金曜の朝").wait_for()
    saved = next(i for i in request("GET", "/api/board", code)["items"] if i["id"] == "a-06")
    report.check(saved["task_summary"] == "もう1台が直した用件", "もう1台の変更が消えた")
    # 親以外の言葉は原文のまま残す。欄を出さず、理由を出す。
    doctor_item = next(
        i
        for i in request("GET", "/api/board", code)["items"]
        if i["text_original"] and i["speaker"] not in (None, "親")
    )
    doctor = show_row(page, doctor_item["id"])
    doctor.get_by_role("button", name="編集").click()
    report.check(
        "原文のまま残します" in doctor.locator("[data-edit]").inner_text(),
        "原文を直せない理由が無い",
    )
    report.check(
        all(
            box.input_value() != doctor_item["text_original"]
            for box in doctor.locator("[data-edit] textarea").all()
        ),
        "親以外の言葉の原文を直せる",
    )
    report.check(
        "作った案" not in doctor.locator("[data-edit]").inner_text(), "編集画面に注記が2つ出ている"
    )
    if doctor_item["task_summary"]:
        report.check(
            doctor_item["text_original"] in doctor.locator("[data-edit]").inner_text(),
            "直せない原文が読めない",
        )
    inspect(page, "20d2-edit-original-fixed", out, report)
    doctor.get_by_role("button", name="戻る").click()
    doctor.locator("button.row-main").click()
    # 取り消すと何も変わらない。
    row.get_by_role("button", name="編集").click()
    panel.get_by_label("担当").fill("別の人")
    panel.get_by_role("button", name="戻る").click()
    row.get_by_text("担当：母").wait_for()

    # 未送信の連絡は文を直せて、送る文も同じになる。医療の語は通さない。
    send_row = page.locator("[data-send='家族']").first
    send_id = send_row.get_attribute("data-item")
    if send_row.locator(".row-body").count() == 0:
        send_row.locator(".row-lead.send").click()
    send_row.get_by_role("button", name="編集").click()
    report.check(
        "そのまま下書きになります" in send_row.locator("[data-edit]").inner_text(),
        "直した文が送られることが書いていない",
    )
    report.check("作った案" not in send_row.locator("[data-edit]").inner_text(), "注記が逆")
    box = send_row.locator("[data-edit] textarea")
    original_text = box.input_value()
    box.fill("薬の量を増やすと伝えて")
    send_row.get_by_role("button", name="保存する").click()
    page.get_by_text("医療の語が入っている文は送れません").wait_for()
    report.check(box.input_value() == "薬の量を増やすと伝えて", "断られて入れた文が消えた")
    box.fill(original_text + " よろしくね")
    send_row.get_by_role("button", name="保存する").click()
    page.get_by_text("直しました").wait_for()
    send_row.get_by_text("よろしくね").wait_for()
    saved = next(i for i in request("GET", "/api/board", code)["items"] if i["id"] == send_id)
    report.check(saved["text_original"].endswith("よろしくね"), "送る文が直っていない")
    # 案から作った連絡は、相手を変えられない（欄を出さない）。
    send_row.get_by_role("button", name="編集").click()
    report.check(send_row.get_by_label("相手").count() == 0, "連絡に相手の欄が出ている")
    send_row.get_by_role("button", name="戻る").click()
    # 検査の続きのために、元の文へ戻す。
    send_row.get_by_role("button", name="編集").click()
    send_row.locator("[data-edit] textarea").fill(original_text)
    send_row.get_by_role("button", name="保存する").click()
    page.wait_for_function(
        """([code, id]) => fetch('/api/board', {headers: {'X-Family-Code': code}})
            .then((r) => r.json())
            .then((b) => !b.items.find((i) => i.id === id).text_original.endsWith('よろしくね'))""",
        arg=[code, send_id],
    )
    send_row.locator(".row-lead.send").click()

    # 相手を変えると、その相手の見出しの下へ動く。
    moved = show_row(page, "a-06")
    before_part = next(
        i["part"] for i in request("GET", "/api/board", code)["items"] if i["id"] == "a-06"
    )
    target_part = "支援" if before_part != "支援" else "職場"
    moved.get_by_role("button", name="編集").click()
    moved.get_by_label("相手").select_option(target_part)
    moved.get_by_role("button", name="保存する").click()
    page.locator(f"[data-part='{target_part}'] [data-item='a-06']").wait_for()
    inspect(page, "20e-edit-moved", out, report)
    # 元へ戻す。
    back = show_row(page, "a-06")
    back.get_by_role("button", name="編集").click()
    back.get_by_label("相手").select_option(before_part)
    back.get_by_label("担当").fill("")
    back.get_by_label("期日").fill("")
    back.get_by_role("button", name="保存する").click()
    page.locator(f"[data-part='{before_part}'] [data-item='a-06']").wait_for()


def round_trip(browser, page: Page, code: str, out: Path, report: Report) -> None:
    """関門2：頼む → 夫が持っていく → 開いたままの母の画面に戻る。"""
    # 自分のこと：一言を残せる。AIを呼ばない。夫のリンクから見える。
    calls_before = len(request("GET", "/api/board", code)["items"])
    from_menu(page, "自分のこと")
    note_box = page.get_by_role("textbox", name="今日の自分を一言")
    note_box.wait_for()
    inspect(page, "20c-own-note", out, report)
    note_box.fill("今日はパンだけ。少し眠れた")
    page.locator(".own-note").get_by_role("button", name="残す").click()
    page.locator("[id='part-親'] [data-item]", has_text="パンだけ").wait_for()
    page.get_by_text("残しました").wait_for()
    report.check(note_box.input_value() == "", "残した後も文が残っている")
    # 2つ目を残すと上に出る。どちらにも時刻が付く。開き直しても残る。
    note_box.fill("今日は売店まで行けた")
    page.locator(".own-note").get_by_role("button", name="残す").click()
    page.locator("[id='part-親'] [data-item]", has_text="売店まで").wait_for()
    own_titles = page.locator("[id='part-親'] .row.own .row-title").all_inner_texts()
    report.check(own_titles[0] == "今日は売店まで行けた", f"新しい順でない: {own_titles}")
    report.check(
        all(
            re.search(r"\d+/\d+ \d+:\d\d", text)
            for text in page.locator("[id='part-親'] .row.own time").all_text_contents()
        ),
        "自分のことに時刻が無い",
    )
    page.reload(wait_until="networkidle")
    report.check(
        page.locator("[id='part-親'] [data-item]", has_text="パンだけ").count() == 1,
        "開き直すと一言が消える",
    )
    note_box.wait_for()
    # 夫の画面の検査が前の件数を見ないよう、2つ目はここで消す。
    version = request("GET", "/api/board", code)["version"]
    second = next(
        i
        for i in request("GET", "/api/board", code)["items"]
        if i["text_original"] == "今日は売店まで行けた"
    )
    request("DELETE", f"/api/board/items/{second['id']}?version={version}", code)
    page.reload(wait_until="networkidle")
    page.locator("[id='part-親'] [data-item]", has_text="パンだけ").wait_for()
    after_note = request("GET", "/api/board", code)
    report.check(len(after_note["items"]) == calls_before + 1, "一言が1件で入っていない")
    report.check(
        (after_note["items"][-1]["part"], after_note["items"][-1]["kind"]) == ("親", "情報"),
        "一言の入り方が違う",
    )
    to_parts(page)
    edit_and_times(page, code, out, report)
    open_sends(page)
    family_card = page.locator("[data-send='家族']")
    childcare_card = page.locator("[data-send='保育園・預け先']")
    report.check(
        family_card.get_by_role("button", name="共有").count() == 0, "使えない共有が出ている"
    )
    report.check(
        "faint"
        in (family_card.get_by_role("button", name="送ったと記録").get_attribute("class") or ""),
        "送る前の［送ったと記録］が薄くなっていない",
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
    childcare_card.get_by_role("button", name="送ったと記録").click()
    moved = page.locator(f"[data-item='{childcare_id}']")
    moved.get_by_text("返事待ち").wait_for()
    moved.get_by_text("送った").wait_for()
    page.get_by_text("相手に渡しました").wait_for()
    # 送った連絡の題は最初の用件の文。挨拶も全文も題にしない。全文は行を開くと読める。
    moved_title = moved.locator(".row-title").inner_text()
    report.check(
        "火曜の延長" in moved_title
        and "お世話" not in moved_title
        and "よろしく" not in moved_title,
        f"送った連絡の題が短くない: {moved_title}",
    )
    moved.locator(".row-main").click()
    report.check("連絡の全文" in moved.inner_text(), "送った連絡の全文が読めない")
    report.check("よろしくお願いします" in moved.inner_text(), "送った連絡の全文が読めない")
    # 履歴：文を作った→送った。送った後の文は直せない。
    labels = moved.locator(".timeline li").all_inner_texts()
    report.check(
        len(labels) == 2 and "文を作った" in labels[0] and "送った" in labels[1],
        f"履歴が違う: {labels}",
    )
    moved.get_by_role("button", name="編集").click()
    moved.get_by_text("送った後の文は直せません").wait_for()
    report.check(moved.locator("[data-edit] textarea").count() == 0, "送った文の欄が出ている")
    inspect(page, "21b-edit-sent", out, report)
    moved.get_by_role("button", name="戻る").click()
    moved.locator(".row-main").click()
    # 取り消しの連絡を送ったので、もとの項目は「取り消し」の表示になる。
    reveal(page.locator("[data-item='a-05']"))
    page.locator("[data-item='a-05']").get_by_text("取り消し").wait_for()
    report.check(
        page.locator("[data-item='a-05']").get_by_role("button", name="返事を記録").count() == 0,
        "取り消した項目に返事のボタンが残っている",
    )

    # 共有：使える端末では大きく出て、家族の文にはリンクが付く。
    page.add_init_script("navigator.share = async (data) => { window.__shared = data }")
    page.reload(wait_until="networkidle")
    open_sends(page)
    family_card.get_by_role("button", name="共有").click()
    family_card.get_by_role("button", name="送ったと記録").wait_for()
    text = page.evaluate("window.__shared.text")
    report.check("着替えを追加" in text, "共有した文に用件が無い")
    link = re.search(r"http\S+/m#\S+", text)
    report.check(link is not None, "共有した文に1件のリンクが無い")
    item_id = family_card.get_attribute("data-item")
    # LINEから戻ると開き直されることがある。［送ったと記録］は残る。
    page.reload(wait_until="networkidle")
    open_sends(page)
    family_card.get_by_role("button", name="送ったと記録").click()
    home_card = page.locator(f"[data-item='{item_id}']")
    home_card.get_by_text("返事待ち").wait_for()

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
    # 夫の画面：ほかのまだのものと、病室からの一言。
    board_now = request("GET", "/api/board", code)
    item_owner = next(i for i in board_now["items"] if i["id"] == item_id)["owner"]
    expected = [
        i
        for i in board_now["items"]
        if i["id"] != item_id
        and i["kind"] == "行動"
        and i["owner"] == item_owner
        and i["status"] not in ("完了", "届いた", "頼みたい")
        and not i["released"]
    ]
    report.check(len(expected) >= 1, "検査の前提：夫のほかの用事が無い")
    todo = h_page.locator("[data-todo]")
    report.check(todo.locator("a").count() == len(expected), "まだのものの件数が違う")
    todo_text = todo.inner_text()
    report.check(
        "未依頼" not in todo_text and "これから" not in todo_text, "送っていないものが一覧にある"
    )
    report.check("答えた：" in todo_text or "返事待ち" in todo_text, f"一覧の語が違う: {todo_text}")
    report.check(
        "パンだけ" in h_page.locator("[data-parent-notes]").inner_text(), "夫に一言が見えない"
    )
    inspect(h_page, "22a-one-list", out, report)
    # 一覧から開くと、その用事の画面になる。
    todo.locator("a").first.click()
    h_page.wait_for_function(f"!location.hash.endsWith('{item_id}')")
    h_page.goto(link[0], wait_until="networkidle")
    h_page.reload(wait_until="networkidle")
    h_page.get_by_role("button", name="持っていく").wait_for()
    # 全部終わると、一覧は出ない。
    for entry in expected:
        request(
            "PATCH",
            f"/api/board/items/{entry['id']}",
            code,
            {"version": request("GET", "/api/board", code)["version"], "status": "完了"},
        )
    h_page.reload(wait_until="networkidle")
    h_page.get_by_role("button", name="持っていく").wait_for()
    report.check(h_page.locator("[data-todo]").count() == 0, "用事が無いのに一覧が出ている")
    for entry in expected:
        request(
            "PATCH",
            f"/api/board/items/{entry['id']}",
            code,
            {"version": request("GET", "/api/board", code)["version"], "status": entry["status"]},
        )
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
    h_page.get_by_text("「持っていく」と伝えました").wait_for()
    inspect(h_page, "23-one-answered", out, report)
    report.check(
        "夫：引き受けた" in h_page.locator("[data-last-event]").inner_text(),
        "夫の画面に答えの時刻が無い",
    )
    home_card.get_by_text("夫：引き受けた").wait_for(timeout=5000)
    report.check("新" in home_card.inner_text(), "母の画面に「新」が付かない")
    report.check(
        re.search(r"\d+/\d+ \d+:\d\d", home_card.inner_text()) is not None,
        "返事に時刻が無い",
    )
    # 閉じていても、見出しだけで返事の中身と「新」が読める。
    family_fold = page.locator("[data-part='家族'] .part-fold")
    family_fold.click()
    fold_text = family_fold.inner_text()
    report.check(
        "夫：引き受けた" in fold_text and "新" in fold_text,
        f"見出しに返事が無い: {fold_text}",
    )
    family_fold.click()
    inspect(page, "24-board-returned", out, report)

    # ◯時なら：答えを変えられ、時刻は選ぶだけ。
    h_page.get_by_role("button", name="答えを変える").click()
    h_page.get_by_role("button", name="◯時なら").click()
    inspect(h_page, "25-one-time", out, report)
    h_page.get_by_role("button", name="明日の朝").click()
    h_page.get_by_text("「明日の朝なら」と伝えました").wait_for()
    home_card.get_by_text("夫：明日の朝なら").wait_for(timeout=5000)

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

    # 押し間違えた返事は、下の帯からすぐ戻せる。送ったと重ねて残さない。
    moved = page.locator(f"[data-item='{childcare_id}']")
    moved.get_by_role("button", name="返事を記録").click()
    moved.get_by_role("button", name="難しい").click()
    page.get_by_text("返事を記録しました").wait_for()
    report.check(moved.get_by_role("button", name="返事を記録").count() == 0, "返事が記録されない")
    inspect(page, "27a-reply-undo", out, report)
    page.locator(".dock").get_by_role("button", name="元に戻す").click()
    page.get_by_text("元に戻しました").wait_for()
    moved.get_by_role("button", name="返事を記録").wait_for()
    report.check(moved.locator(".reply-panel").count() == 0, "戻すと返事の欄が開いたまま")
    labels = [
        event["label"]
        for event in next(
            i for i in request("GET", "/api/board", code)["items"] if i["id"] == childcare_id
        )["events"]
    ]
    report.check(
        labels[-1] == "元に戻した" and labels.count("送った") == 1, f"履歴が違う: {labels}"
    )

    # 返事が来た：［◯時まで］で記録する。ほかの返答待ちは残る。AIは使わない。
    moved.get_by_role("button", name="返事を記録").click()
    moved.get_by_role("button", name="◯時まで").wait_for()
    inspect(page, "28-reply-open", out, report)
    moved.get_by_role("button", name="◯時まで").click()
    moved.locator("input[type=time]").fill("18:30")
    inspect(page, "29-reply-time", out, report)
    moved.get_by_role("button", name="記録する").click()
    moved.get_by_text("18:30まで可").wait_for()
    report.check(
        moved.get_by_role("button", name="返事を記録").count() == 0, "答えの後も返事のボタンが残る"
    )
    # 取り消した項目は表示だけ変わる。台帳の状態は変えない。
    other = next(i for i in request("GET", "/api/board", code)["items"] if i["id"] == "a-05")
    report.check(other["status"] == "頼んだ", "ほかの返答待ちが変わった")
    inspect(page, "30-board-replied", out, report)
    # 帯が消えた後でも、行を開けば戻せる。「◯時まで」は返事の中身なので、一緒に消える。
    show_row(page, childcare_id)
    moved.get_by_role("button", name="元に戻す").click()
    moved.get_by_role("button", name="返事を記録").wait_for()
    undone = next(i for i in request("GET", "/api/board", code)["items"] if i["id"] == childcare_id)
    report.check(
        undone["status"] == "頼んだ" and undone["due"] != "18:30",
        f"戻し先が違う: {undone['status']}",
    )
    # 検査の続きのために、同じ返事を入れ直して行を閉じる。
    moved.get_by_role("button", name="返事を記録").click()
    moved.get_by_role("button", name="◯時まで").click()
    moved.locator("input[type=time]").fill("18:30")
    moved.get_by_role("button", name="記録する").click()
    moved.get_by_text("18:30まで可").first.wait_for()
    moved.locator(".row-main").click()


def next_step_order(browser, out: Path, report: Report) -> None:
    """次にできることは1つ。順は、下書き→「難しい」→新しい返事→これからの用事。"""
    context = browser.new_context(
        viewport={"width": WIDTH, "height": HEIGHT},
        device_scale_factor=2,
        color_scheme="light",
        has_touch=True,
    )
    page = context.new_page()
    page.goto(BASE + "/", wait_until="networkidle")
    page.get_by_role("button", name="家庭Bで始める").click()
    to_now(page)
    code = page.evaluate("location.hash.slice(1)")
    page.locator("[data-save-link]").get_by_role("button", name="あとで").click()
    inspect(page, "b-now", out, report)

    def set_status(item_id: str, status: str) -> None:
        version = request("GET", "/api/board", code)["version"]
        request(
            "PATCH", f"/api/board/items/{item_id}", code, {"version": version, "status": status}
        )

    def next_text() -> str:
        page.reload(wait_until="networkidle")
        section = page.locator("[data-next-step]")
        section.get_by_role("heading", name="次にできること").wait_for()
        return section.inner_text()

    board = request("GET", "/api/board", code)
    # 頼める人がいない家庭で「夫」を文に出さない。
    report.check("夫に" not in next_text(), "家庭Bの次にできることに夫が出ている")
    # 全部を済にすると、することがない。
    for item in board["items"]:
        if item["kind"] == "行動" and item["status"] != "完了":
            set_status(item["id"], "完了")
        elif item["kind"] == "質問":
            set_status(item["id"], "答えあり")
    text = next_text()
    report.check("今することはありません" in text, f"何も無いとき: {text}")
    report.check(page.locator("[data-next-step] [data-primary]").count() == 0, "主なボタンがある")

    # これからの用事。
    version = request("GET", "/api/board", code)["version"]
    added = request(
        "POST",
        "/api/board/items",
        code,
        {
            "version": version,
            "kind": "行動",
            "part": "支援",
            "certainty": "見込み",
            "task_summary": "登録の確認",
            "speaker": "親",
            "status": "頼みたい",
        },
    )
    todo_id = added["items"][-1]["id"]
    text = next_text()
    report.check("登録の確認" in text and "用事を見る" in text, f"これからの用事: {text}")
    # 引き受けた。主なボタンは完了にする。
    set_status(todo_id, "引き受けた")
    text = next_text()
    report.check("終わったら完了にする" in text, f"引き受けたとき: {text}")
    report.check("登録の確認は、" in text, f"何を引き受けたかが無い: {text}")
    report.check(
        "から返事が届きました" in page.locator("[data-now-reply]").inner_text(),
        "返事のカードが無い",
    )
    report.check(
        re.search(r"\d+/\d+ \d+:\d\d", page.locator("[data-now-reply]").inner_text()) is not None,
        "返事のカードに時刻が無い",
    )
    inspect(page, "b-now-reply", out, report)
    # 難しい。別の手を決める。
    set_status(todo_id, "難しい")
    text = next_text()
    report.check("難しい" in text and "別の手を決める" in text, f"難しいとき: {text}")
    # 医療の問いを入れたら、次にできることは固定文だけ。
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
            "text_original": "薬を増やしたほうがいいですか",
            "speaker": "親",
            "medical": "ask",
            "status": "未確認",
        },
    )
    text = next_text()
    report.check(MEDICAL_FIXED_TEXT in text, f"医療の問いのとき固定文が無い: {text}")
    report.check(
        page.locator("[data-next-step] [data-primary]").count() == 0, "固定文の横に主なボタンがある"
    )
    color = page.locator("[data-now-medical]").evaluate("el => getComputedStyle(el).color")
    report.check(not is_reddish(color), f"固定文に赤を使っている: {color}")
    report.check(
        page.locator("body").inner_text().count(MEDICAL_FIXED_TEXT) == 1, "固定文が2回出ている"
    )
    inspect(page, "b-now-medical", out, report)
    # ［わかった］で閉じると、次の用事へ戻る。足元の固定文も戻る。開き直しても閉じたまま。
    page.locator("[data-next-step]").get_by_role("button", name="わかった").click()
    page.locator("[data-now-medical]").wait_for(state="detached")
    report.check(page.locator(".fixed-note").count() == 1, "閉じた後に足元の固定文が戻らない")
    page.reload(wait_until="networkidle")
    page.locator("[data-next-step]").wait_for()
    report.check(page.locator("[data-now-medical]").count() == 0, "閉じた固定文がまた出た")
    context.close()


def top_and_rows(browser, out: Path, report: Report) -> None:
    """上の帯、下のナビ、メニュー、予定の欄。案の前は節の日付だけ、案の後は候補日だけ。"""
    context = browser.new_context(
        viewport={"width": WIDTH, "height": HEIGHT},
        device_scale_factor=2,
        color_scheme="light",
        has_touch=True,
    )
    page = context.new_page()
    page.goto(BASE + "/", wait_until="networkidle")
    page.get_by_role("button", name="家庭Aで始める").click()
    to_now(page)
    code = page.evaluate("location.hash.slice(1)")
    page.locator("[data-save-link]").get_by_role("button", name="あとで").click()
    page.evaluate("localStorage.setItem('ensemble.consented', 'true')")
    page.reload(wait_until="networkidle")
    page.locator("[data-next-step]").wait_for()

    # 入口は「いま」。開き直しても読み直しても、今の画面のまま。
    report.check(
        page.locator(".bottom-nav [aria-current='page']").inner_text().strip() == "いま",
        "入口が「いま」でない",
    )
    labels = [text.strip() for text in page.locator(".bottom-nav button").all_inner_texts()]
    report.check(labels == ["いま", "やりとり", "進み具合"], f"ナビの語が違う: {labels}")
    # 初期データの返事を「届きました」と言わない。
    report.check(
        page.locator("[data-now-reply]").count() == 0, "開いた直後に返事のカードが出ている"
    )
    report.check(
        "終わったら完了" not in page.locator("[data-next-step]").inner_text(),
        "初期データの返事を完了へ促している",
    )
    # 何をするかが最初に見える。主なボタンは1画面の中。
    tops = page.evaluate("""() => ['[data-next-step]', '[data-schedule]'].map(
      (s) => document.querySelector(s).getBoundingClientRect().top)""")
    report.check(tops[0] < tops[1], "次にできることが予定より下にある")
    body = page.locator("body").inner_text()
    report.check("ボード" not in body, "「ボード」が画面に残っている")

    # 案の前：医師の言葉の節は原文のまま。日付は別の列。「候補」の札は出さない。
    schedule = page.locator("[data-schedule]")
    clauses = schedule.locator("[data-clause]")
    report.check(clauses.count() == 4, f"節の数が違う: {clauses.count()}")
    report.check("「明日の採血しだい」" in clauses.first.inner_text(), "節が原文のままでない")
    report.check(clauses.first.locator(".clause-days").count() == 1, "日付が別の列にない")
    report.check("候補" not in schedule.inner_text(), "案の前に「候補」の札が出ている")
    report.check("未定" not in schedule.inner_text(), "言っていないことが出ている")
    report.check(page.locator("[data-after-entry]").count() == 1, "結果を入れる入口が無い")
    inspect(page, "now-before-plan", out, report)

    # メニュー。ナビにない入口はここに集める。
    from_menu(page, "入れた情報・原文")
    page.locator("[data-part='職場']").wait_for()
    page.get_by_role("button", name="メニュー", exact=True).click()
    page.locator("[data-menu]").wait_for()
    inspect(page, "menu", out, report)
    menu_text = page.locator("[data-menu]").inner_text()
    for word in ("あなたとご家族のこと", "家族に渡すリンク", "しおりから探す", "カレンダー", "進み具合",
                 "自分のこと", "ゴミ箱", "データと共有の設定"):  # fmt: skip
        report.check(word in menu_text, f"メニューに「{word}」が無い")
    report.check(page.locator(".bottom-nav").count() == 0, "メニューにナビが出ている")
    page.get_by_role("button", name="自分のこと", exact=True).click()
    page.locator("[data-part='親'] .own-note").wait_for()
    report.check(
        "今日はまだ何も食べられていない" in page.locator("[data-part='親']").inner_text()
        or page.locator("[data-part='親'] .own-note").count() == 1,
        "自分のことが開かない",
    )
    inspect(page, "own", out, report)
    fixed_note(page, "自分のこと", report)

    # ⑤：節を選ぶだけで結果が入る。AIは案の1回だけ。
    to_now(page)
    page.locator("[data-after-entry]").click()
    options = page.locator("[data-option]")
    options.first.wait_for()
    report.check(options.count() == 2, f"選択肢が{options.count()}つ")
    report.check(
        "だめなら月曜にもう一回とって、それで大丈夫なら火曜かな" in options.nth(1).inner_text(),
        "「それで」の節が前の節につながっていない",
    )
    submit = page.locator(".dock [data-primary]")
    report.check(submit.is_disabled(), "選ぶ前に進める")
    inspect(page, "after", out, report)
    fixed_note(page, "説明を聞いたあと", report)
    # まだ聞いていない、で戻れる。何も増えない。
    before = len(request("GET", "/api/board", code)["items"])
    page.get_by_role("button", name="まだ説明を聞いていない").click()
    page.locator("[data-next-step]").wait_for()
    report.check(
        len(request("GET", "/api/board", code)["items"]) == before, "戻っただけで台帳が増えた"
    )
    # 選んで進むと、案を待つ間も止められる。下の帯から取り消せる。
    page.locator("[data-after-entry]").click()
    options.nth(1).click()
    submit.click()
    page.locator("[data-next-step]").wait_for()
    undo = page.locator(".dock").get_by_role("button", name="元に戻す")
    undo.wait_for()
    added = request("GET", "/api/board", code)["items"][-1]
    first_day = request("GET", "/api/board", code)["doctor"]["clauses"][0]["days"][0].split("（")[0]
    report.check(
        added["text_original"] == f"{first_day}の採血はだめでした",
        f"入れた文: {added['text_original']}",
    )
    report.check(
        added["text_original"] in page.locator(".dock").inner_text(), "入れた文が帯に出ない"
    )
    report.check(added["certainty"] == "確定", "結果が確定のままでない")
    report.check(added["refers_to"] == "a-07", "結果が医師の言葉を指していない")
    undo.click()
    page.get_by_text("ゴミ箱に入れました").wait_for()
    report.check(
        all(item["id"] != added["id"] for item in request("GET", "/api/board", code)["items"]),
        "取り消しても台帳に残っている",
    )
    report.check(no_plan(page), "取り消した結果の案が残っている")
    # 全部終わったいまは、空のいまと分ける。予定と入力の行は残る。
    to_now(page)
    page.locator("[data-after-entry]").click()
    options.nth(1).click()
    submit.click()
    plan_button(page).wait_for(timeout=15000)
    inspect(page, "now-plan-ready", out, report)
    report.check(
        page.locator("[data-next-step] [data-primary]").count() == 1, "主なボタンが1つでない"
    )

    # 案を作れないとき：通信の失敗は［もう一度］、⑤の直後は［元に戻す］も。上限は押し直しを出さない。
    states_in_failure(page, code, report)
    # 案を進めると、候補日と札が出る。取り消した日は残す。
    # 読み進めた位置から押しても、案の画面は頭から開く（デモ動画作成の指摘）。
    plan_button(page)
    page.evaluate("scrollTo(0, document.documentElement.scrollHeight)")
    page.wait_for_timeout(100)
    page.get_by_role("button", name="変わるところを見る").click()
    page.get_by_role("button", name="下書きを作る").wait_for()
    page.wait_for_timeout(100)
    report.check(page.evaluate("scrollY") == 0, "案の画面が途中から開く")
    page.get_by_role("button", name="下書きを作る").click()
    back_from_map(page)
    to_now(page)
    report.check(page.locator("[data-clause]").count() == 0, "案の後に節が残っている")
    days = page.locator("[data-candidate-day]")
    report.check(days.count() == 2, f"候補日の数が違う: {days.count()}")
    day_text = schedule.inner_text()
    report.check("取り消し" in day_text and "候補" in day_text, f"札が無い: {day_text}")
    report.check("すべて取り消し" not in day_text, "候補が残っているのに「すべて取り消し」")
    inspect(page, "now-after-plan", out, report)
    send = page.locator("[data-next-step]").get_by_role(
        "button", name=re.compile("人分を見る|下書きを見る")
    )
    report.check(send.count() == 1, "下書きの主なボタンが無い")
    context.close()


def states_in_failure(page: Page, code: str, report: Report) -> None:
    """案を待っている案を閉じて、失敗の見せ方を確かめ、もとの案へ戻す。"""
    section = page.locator("[data-next-step]")
    answer = {"status": 503, "detail": "案を出せませんでした。入れた文は残っています"}

    def fail(route) -> None:
        route.fulfill(
            status=answer["status"],
            content_type="application/json",
            body=json.dumps({"detail": answer["detail"]}, ensure_ascii=False),
        )

    # いまの案をやめて、結果をもう一度入れる。
    plan_button(page).click()
    page.get_by_role("button", name="この案をやめる").click()
    section.wait_for()
    items = request("GET", "/api/board", code)
    result = items["items"][-1]
    request("DELETE", f"/api/board/items/{result['id']}?version={items['version']}", code)
    page.reload(wait_until="networkidle")
    page.route("**/api/board/proposal", fail)
    page.locator("[data-after-entry]").click()
    page.locator("[data-option='1']").click()
    page.locator(".dock [data-primary]").click()
    section.get_by_role("button", name="もう一度").wait_for()
    text = section.inner_text()
    report.check("案を出せませんでした" in text, f"失敗の文が無い: {text}")
    report.check("選んだ内容は残っています" in text, f"⑤の直後の文が無い: {text}")
    report.check(section.get_by_role("button", name="元に戻す").count() == 1, "⑤の直後に戻せない")
    inspect(page, "now-503-after", OUT_FOR_STATES, report)
    # 回数の上限。押し直しを出さない。
    answer.update(status=429, detail="今日の回数の上限に届きました。入れた文は残っています")
    section.get_by_role("button", name="もう一度").click()
    section.get_by_text("今日の回数の上限に届きました").wait_for()
    report.check(
        section.get_by_role("button", name="もう一度").count() == 0, "上限で押し直しが出た"
    )
    inspect(page, "now-429", OUT_FOR_STATES, report)
    section.get_by_role("button", name="閉じる").click()
    page.unroute("**/api/board/proposal", fail)
    # 同じ結果の項目を消して、通った案へ戻す。
    items = request("GET", "/api/board", code)
    result = items["items"][-1]
    request("DELETE", f"/api/board/items/{result['id']}?version={items['version']}", code)
    page.reload(wait_until="networkidle")
    page.locator("[data-after-entry]").click()
    page.locator("[data-option='1']").click()
    page.locator(".dock [data-primary]").click()
    plan_button(page).wait_for(timeout=15000)


def lanes(browser, out: Path, report: Report) -> None:
    """進み具合：上の1行とまとまり。返事を記録できて、時刻があり、数が列と合う。"""
    context = browser.new_context(
        viewport={"width": WIDTH, "height": HEIGHT},
        device_scale_factor=2,
        color_scheme="light",
        has_touch=True,
    )
    page = context.new_page()
    page.goto(BASE + "/", wait_until="networkidle")
    page.get_by_role("button", name="家庭Aで始める").click()
    to_now(page)
    code = page.evaluate("location.hash.slice(1)")
    page.locator("[data-save-link]").get_by_role("button", name="あとで").click()
    tab(page, "進捗別")

    def count(word: str) -> int:
        return int(page.locator(f"[data-column='{word}']").inner_text().replace(word, "").strip())

    def rows_in(*groups: str) -> int:
        return sum(page.locator(f"[data-lane='{group}'] .row").count() for group in groups)

    def consistent() -> None:
        report.check(
            count("これから") == rows_in("difficult", "send", "older", "todo"),
            "「これから」の数と行が合わない",
        )
        report.check(count("返事待ち") == rows_in("waiting"), "「返事待ち」の数と行が合わない")
        report.check(
            count("引き受けた") == rows_in("accepted", "timed"), "「引き受けた」の数と行が合わない"
        )

    consistent()
    inspect(page, "lanes", out, report)
    # 返事が2件以上のとき、カードは新しい1件だけ。残りは進み具合へ。
    page.wait_for_timeout(1100)  # 時刻は秒までなので、開いた時刻より後にする。
    for item_id in ("a-06", "a-02"):
        version = request("GET", "/api/board", code)["version"]
        request(
            "PATCH",
            f"/api/board/items/{item_id}",
            code,
            {"version": version, "status": "引き受けた"},
        )
        page.wait_for_timeout(1100)
    to_now(page)
    page.locator("[data-now-reply]").wait_for()
    more = page.locator("[data-more-replies]")
    report.check("ほかの返事を見る（1件）" in more.inner_text(), "ほかの返事の入口が無い")
    inspect(page, "now-two-replies", out, report)
    more.click()
    page.locator("[data-lanes]").wait_for()
    for item_id in ("a-06", "a-02"):
        version = request("GET", "/api/board", code)["version"]
        request("POST", f"/api/board/items/{item_id}/undo", code, {"version": version})
    page.reload(wait_until="networkidle")
    body = page.locator("[data-lanes]").inner_text()
    for word in ("あなた", "相手"):
        report.check(word not in body.split(), f"前の札の語「{word}」が残っている")
    times = page.locator("[data-lanes] .row-meta time").all_text_contents()
    report.check(
        len(times) > 0 and all(re.search(r"\d+/\d+ \d+:\d\d", text) for text in times),
        f"カードに時刻が無い: {times}",
    )
    # 職場・保育園は、送った後に［返事を記録］が押せる。「18:30まで可」と「返事待ち」が同じ画面に見える。
    waiting = page.locator("[data-lane='waiting'] .row").first
    report.check(
        waiting.get_by_role("button", name="返事を記録").count() == 1,
        "返事待ちに［返事を記録］が無い",
    )
    waiting.get_by_role("button", name="返事を記録").click()
    waiting.get_by_role("button", name="◯時まで").click()
    waiting.get_by_label("何時までか").fill("18:30")
    waiting.get_by_role("button", name="記録する").click()
    page.locator("[data-lane='timed']").wait_for()
    report.check("18:30" in page.locator("[data-lane='timed']").inner_text(), "時刻の返事が出ない")
    consistent()
    inspect(page, "lanes-timed", out, report)
    # 元に戻せる。
    page.locator(".dock").get_by_role("button", name="元に戻す").click()
    page.get_by_text("元に戻しました").wait_for()
    page.locator("[data-lane='waiting']").wait_for()
    consistent()
    # 丸を押すと済の畳みへ。
    first = page.locator("[data-lane='todo'] .row").first
    first_id = first.get_attribute("data-item")
    first.locator(".row-lead.check").click()
    page.get_by_text("済にしました").wait_for()
    done = page.locator(f"[data-done-fold] [data-item='{first_id}']")
    done.wait_for(state="attached")
    consistent()
    fixed_note(page, "進み具合", report)
    report.check(code != "", "コードが無い")
    context.close()


def from_link(browser, out: Path, report: Report) -> None:
    """夫のリンクから開いたタブは親の視点の語にし、日数を出さない（§8.7）。"""
    context = browser.new_context(
        viewport={"width": WIDTH, "height": HEIGHT},
        device_scale_factor=2,
        color_scheme="light",
        has_touch=True,
    )
    parent = context.new_page()
    parent.goto(BASE + "/", wait_until="networkidle")
    parent.get_by_role("button", name="家庭Aで始める").click()
    to_parts(parent)
    code = parent.evaluate("location.hash.slice(1)")
    parent.locator("[data-save-link]").get_by_role("button", name="あとで").click()
    version = request("GET", "/api/board", code)["version"]
    request("PATCH", "/api/board/items/a-02", code, {"version": version, "status": "頼んだ"})
    later = datetime.now() + timedelta(days=2)
    # 親のタブ：いつもの語と日数。
    parent.clock.set_fixed_time(later)
    parent.reload(wait_until="networkidle")
    open_parts(parent)
    report.check(
        "送って2日" in parent.locator("[data-item='a-02']").inner_text(), "親のタブに日数が無い"
    )
    to_now(parent)
    report.check(
        "次にできること" in parent.locator("[data-next-step]").inner_text(), "親のタブの語が違う"
    )
    to_parts(parent)
    # 夫のタブ：1件の画面から［ボード全体を見る］で開く。
    husband = context.new_page()
    husband.clock.set_fixed_time(later)
    husband.goto(f"{BASE}/m#{code}/a-02", wait_until="networkidle")
    husband.get_by_role("link", name="いまの状況を見る").click()
    husband.locator("[data-lanes]").wait_for()
    report.check(
        husband.get_by_role("button", name="自分の用事に戻る").count() == 1, "夫が用事へ戻れない"
    )
    to_parts(husband)
    open_parts(husband)
    heads = " ".join(husband.locator(".part-counts").all_inner_texts())
    report.check("あなた" not in heads and "相手" not in heads, f"夫のタブの見出し: {heads}")
    report.check(
        "送って" not in husband.locator("[data-item='a-02']").inner_text(),
        "夫のタブに催促に読める日数が出ている",
    )
    tab(husband, "進捗別")
    report.check(
        "あなた" not in husband.locator("[data-lanes]").inner_text(), "夫のタブに「あなた」"
    )
    inspect(husband, "2n-lanes-from-link", out, report)
    # 「いま」は親の視点なので出さない。開き直しても同じタブなら変わらない。親のタブはそのまま。
    labels = [text.strip() for text in husband.locator(".bottom-nav button").all_inner_texts()]
    report.check(labels == ["やりとり", "進み具合"], f"夫のタブのナビ: {labels}")
    report.check(
        husband.locator("[data-column]").first.inner_text().startswith("親がこれから"),
        "夫のタブの上の1行の語が違う",
    )
    husband.reload(wait_until="networkidle")
    report.check(husband.locator("[data-next-step]").count() == 0, "夫のタブに「いま」が出た")
    husband.get_by_role("button", name="メニュー", exact=True).click()
    menu_text = husband.locator("[data-menu]").inner_text()
    report.check("あなたとご家族のこと" not in menu_text, "夫のメニューに家族設定が出ている")
    report.check("この記録" not in menu_text, "夫のメニューに消す入口が出ている")
    close_menu(husband)
    husband.get_by_role("button", name="自分の用事に戻る").click()
    husband.get_by_role("heading", name="お願いが1件あります").wait_for()
    parent.reload(wait_until="networkidle")
    to_now(parent)
    report.check(
        "次にできること" in parent.locator("[data-next-step]").inner_text(),
        "夫のタブの印が親のタブへ移った",
    )
    context.close()


def older_family(browser, out: Path, report: Report) -> None:
    """前の案の家族の文は、ボードでも地図でも送る文に数えない（QA TestのP1-A）。"""
    context = browser.new_context(
        viewport={"width": WIDTH, "height": HEIGHT},
        device_scale_factor=2,
        color_scheme="light",
        has_touch=True,
    )
    page = context.new_page()
    page.goto(BASE + "/", wait_until="networkidle")
    page.get_by_role("button", name="家庭Aで始める").click()
    to_parts(page)
    code = page.evaluate("location.hash.slice(1)")
    page.locator("[data-save-link]").get_by_role("button", name="あとで").click()
    for branch_id, draft in (
        ("x", "前の案の家族への連絡です"),
        ("y", "新しい案の家族への連絡です"),
    ):
        version = request("GET", "/api/board", code)["version"]
        request(
            "POST",
            "/api/board/branches",
            code,
            {
                "version": version,
                "branch": {
                    "id": branch_id,
                    "condition_item_id": "a-03",
                    "candidate_days": [],
                    "outcomes": [
                        {
                            "label": "そのとき",
                            "actions": [
                                {
                                    "part": "家族",
                                    "type": "伝える",
                                    "draft": draft,
                                    "basis_ids": ["a-03"],
                                    "release": [],
                                }
                            ],
                        }
                    ],
                },
            },
        )
    page.reload(wait_until="networkidle")
    to_now(page)
    top = page.locator("[data-next-step]").inner_text()
    report.check("ほか" not in top and "下書きを見る" in top, f"「いま」の下書きの数が違う: {top}")
    open_map(page)
    page.get_by_text("まだ送っていません", exact=False).first.wait_for()
    page.wait_for_timeout(300)
    text = page.locator("body").inner_text()
    report.check("下書きが1件あります" in text, "地図が前の案の家族の文まで数えている")
    context.close()


def tab_position(browser, out: Path, report: Report) -> None:
    """下のナビを押すと、開いた画面の頭から見える。どの大きさでも（QA TestのP1-B）。"""
    pairs = ("いま", "やりとり", "進み具合")
    for width, height in ((360, 740), (360, 800), (390, 844), (412, 915)):
        context = browser.new_context(
            viewport={"width": width, "height": height},
            device_scale_factor=2,
            color_scheme="light",
            has_touch=True,
        )
        page = context.new_page()
        page.goto(BASE + "/", wait_until="networkidle")
        page.get_by_role("button", name="家庭Aで始める").click()
        to_parts(page)
        page.locator("[data-save-link]").get_by_role("button", name="あとで").click()
        for start in pairs:
            for goal in pairs:
                nav(page, start)
                page.wait_for_timeout(40)
                page.evaluate("scrollTo(0, document.documentElement.scrollHeight)")
                page.wait_for_timeout(40)
                nav(page, goal)
                page.wait_for_timeout(80)
                report.check(
                    page.evaluate("scrollY") == 0,
                    f"{width}x{height} {start}→{goal}：開いた画面が頭から見えない",
                )
        # カレンダーの行からやりとりへ飛ぶ動きは、行の場所へ行くまま。
        tab(page, "週の予定")
        page.locator("[data-week-item]").first.click()
        page.wait_for_timeout(700)
        report.check(
            page.locator(".bottom-nav [aria-current='page']").inner_text().strip() == "やりとり",
            "週の行を押してもやりとりへ行かない",
        )
        context.close()


def strip_frame(browser, out: Path, report: Report) -> None:
    """地図の帯の候補の枠は、文字に触れず、マスに収まる（デモ動画作成の試し撮り）。"""
    context = browser.new_context(
        viewport={"width": WIDTH, "height": HEIGHT},
        device_scale_factor=3,
        color_scheme="light",
        has_touch=True,
    )
    page = context.new_page()
    page.goto(BASE + "/", wait_until="networkidle")
    page.get_by_role("button", name="家庭Aで始める").click()
    to_parts(page)
    code = page.evaluate("location.hash.slice(1)")
    page.locator("[data-save-link]").get_by_role("button", name="あとで").click()
    version = request("GET", "/api/board", code)["version"]
    request(
        "POST",
        "/api/board/branches",
        code,
        {
            "version": version,
            "branch": {
                "id": "x",
                "condition_item_id": "a-07",
                "candidate_days": [
                    {"day": "木曜", "certainty": "見込み", "cancelled": True},
                    {"day": "火曜", "certainty": "見込み"},
                ],
                "outcomes": [
                    {
                        "label": "そのとき",
                        "actions": [
                            {
                                "part": "家族",
                                "type": "伝える",
                                "draft": "洗濯物をお願いします",
                                "basis_ids": ["a-03", "a-07"],
                                "release": [],
                            }
                        ],
                    }
                ],
            },
        },
    )
    page.reload(wait_until="networkidle")
    open_map(page)
    marks = page.locator(".week-strip .likely .strip-mark")
    marks.first.wait_for()
    page.wait_for_timeout(800)
    # 幅が狭いときや文字が大きいときも、候補は1行のまま（QA TestのP2）。
    for width, height, zoom in (
        (320, 700, 100),
        (320, 700, 200),
        (360, 740, 100),
        (360, 740, 120),
        (360, 740, 200),
        (375, 740, 120),
        (390, 844, 130),
        (412, 915, 130),
    ):
        page.set_viewport_size({"width": width, "height": height})
        page.evaluate(f"document.documentElement.style.fontSize = '{93.75 * zoom / 100}%'")
        page.wait_for_timeout(120)
        for index in range(marks.count()):
            tall = marks.nth(index).evaluate("el => el.getBoundingClientRect().height")
            report.check(
                tall <= 30, f"地図の帯の候補が2行に割れる（幅{width} 文字{zoom}% 高さ{tall:.0f}px）"
            )
        page.locator(".week-strip").screenshot(path=str(out / f"strip-frame-{width}-{zoom}.png"))
        wide = page.evaluate("document.documentElement.scrollWidth - innerWidth")
        report.check(wide <= 0, f"地図の帯で画面が横に動く（幅{width} 文字{zoom}% {wide}px）")
    page.evaluate("document.documentElement.style.fontSize = ''")
    page.set_viewport_size({"width": WIDTH, "height": HEIGHT})
    page.wait_for_timeout(120)
    page.locator(".week-strip").screenshot(path=str(out / "strip-frame.png"))
    for index in range(marks.count()):
        sides = marks.nth(index).evaluate("""el => {
              const box = el.getBoundingClientRect()
              const cell = el.parentElement.getBoundingClientRect()
              const range = document.createRange()
              range.selectNodeContents(el)
              const text = range.getBoundingClientRect()
              return {
                left: text.left - box.left,
                right: box.right - text.right,
                top: text.top - box.top,
                bottom: box.bottom - text.bottom,
                room: Math.min(box.left - cell.left, cell.right - box.right, cell.bottom - box.bottom),
              }
            }""")
        report.check(
            min(sides["left"], sides["right"]) >= 3,
            f"候補の枠の左右に文字が触れる: {sides}",
        )
        report.check(
            min(sides["top"], sides["bottom"]) >= 0,
            f"候補の枠の上下に文字がはみ出す: {sides}",
        )
        # 点線は枠の外へ1.5px出るので、その分もマスに残す。
        report.check(sides["room"] >= 1.5, f"候補の枠がマスから出る: {sides}")
    # 週の予定の帯は別の決まりなので、この余白を当てない。
    page.get_by_role("button", name="戻る", exact=True).click()
    tab(page, "週の予定")
    week_mark = page.locator(".week-nav .likely .strip-mark").first
    week_mark.wait_for()
    report.check(
        week_mark.evaluate("el => getComputedStyle(el).paddingLeft") == "0px",
        "週の予定の帯に地図の帯の余白が当たる",
    )
    context.close()


def dock_undo(browser, out: Path, report: Report) -> None:
    """下の帯の［元に戻す］は、その知らせの操作だけを戻す。"""
    context = browser.new_context(
        viewport={"width": WIDTH, "height": HEIGHT},
        device_scale_factor=2,
        color_scheme="light",
        has_touch=True,
    )
    page = context.new_page()
    page.goto(BASE + "/", wait_until="networkidle")
    page.get_by_role("button", name="家庭Aで始める").click()
    to_parts(page)
    code = page.evaluate("location.hash.slice(1)")
    page.locator("[data-save-link]").get_by_role("button", name="あとで").click()
    page.evaluate("localStorage.setItem('ensemble.consented', 'true')")
    page.reload(wait_until="networkidle")
    to_parts(page)
    dock_undo_button = page.locator(".dock").get_by_role("button", name="元に戻す")

    def status_of(item_id: str) -> str:
        items = request("GET", "/api/board", code)["items"]
        return next(i for i in items if i["id"] == item_id)["status"]

    # もう1台が先に戻したら、さらに戻さない。
    third = page.locator("[data-item='a-03']")
    third_before = status_of("a-03")
    third.locator(".row-lead.check").click()
    page.get_by_text("済にしました").wait_for()
    version = request("GET", "/api/board", code)["version"]
    request("POST", "/api/board/items/a-03/undo", code, {"version": version})
    page.wait_for_function(
        """() => !document.querySelector("[data-item='a-03'] .row-lead.done")""", timeout=7000
    )
    dock_undo_button.click()
    page.get_by_text("もう変わっているので、戻しませんでした").wait_for()
    report.check(status_of("a-03") == third_before, "もう1台が戻した後に、さらに戻った")

    # 案のもとの文を直すと、前の文で作った案は取り下げる。
    add_text(page, "今日の採血はだめでした")
    plan_button(page).wait_for(timeout=15000)
    to_parts(page)
    source = next(
        i
        for i in request("GET", "/api/board", code)["items"]
        if i["text_original"] == "今日の採血はだめでした"
    )
    row = show_row(page, source["id"])
    row.get_by_role("button", name="編集").click()
    row.locator("[data-edit] textarea").last.fill("採血は問題ありませんでした")
    row.get_by_role("button", name="保存する").click()
    page.get_by_text("前の文で作った案は取り下げました").wait_for()
    inspect(page, "20e-edit-dropped-proposal", out, report)
    report.check(no_plan(page), "もとの文を直しても古い案が残る")

    # ［送ったと記録］の押し間違いは、帯からも、後で行の中からも戻せる。
    add_text(page, "今日の採血はだめでした")
    plan_button(page).wait_for(timeout=15000)
    plan_button(page).click()
    page.get_by_role("button", name="下書きを作る").click()
    back_from_map(page)
    open_sends(page)
    send_row = page.locator("[data-send='家族']").first
    send_id = send_row.get_attribute("data-item")
    send_row.get_by_role("button", name="送ったと記録").click()
    page.get_by_text("相手に渡しました").wait_for()
    moved = page.locator(f"[data-item='{send_id}']")
    inspect(page, "20f-asked-undo", out, report)
    dock_undo_button.click()
    page.get_by_text("元に戻しました").wait_for()
    moved.locator(".row-lead.send").wait_for()
    report.check(status_of(send_id) == "頼みたい", "送ったと記録を戻せない")
    if moved.locator(".row-body").count() == 0:
        moved.locator(".row-lead.send").click()
    moved.get_by_role("button", name="送ったと記録").click()
    page.get_by_text("相手に渡しました").wait_for()
    report.check(moved.locator(".row-lead.send").count() == 0, "送った後も送る印が残る")
    show_row(page, send_id)
    moved.get_by_role("button", name="元に戻す").click()
    moved.locator(".row-lead.send").wait_for()
    report.check(status_of(send_id) == "頼みたい", "行の中から送ったと記録を戻せない")
    # 偽のAIの案は a-06 を根拠に使うので、消す場面は案の後に置く。
    # 丸を押した後に別の項目を消すと、帯に前の［元に戻す］を残さない。
    first = page.locator("[data-item='a-01']")
    before = status_of("a-01")
    first.locator(".row-lead.check").click()
    page.get_by_text("済にしました").wait_for()
    target = show_row(page, "a-06")
    target.get_by_role("button", name="消す").click()
    page.locator(".dock [role='alertdialog']").get_by_role(
        "button", name="消す", exact=True
    ).click()
    page.get_by_text("ゴミ箱に入れました").wait_for()
    report.check(dock_undo_button.count() == 0, "消した知らせに前の操作の［元に戻す］が残る")
    report.check(status_of("a-01") != before, "別の項目が戻った")
    context.close()


def older_proposal(browser, out: Path, report: Report) -> None:
    """前の案の未送信の文は「前の案」と分け、送る文の数と地図に矛盾を作らない。"""
    context = browser.new_context(
        viewport={"width": WIDTH, "height": HEIGHT},
        device_scale_factor=2,
        color_scheme="light",
        has_touch=True,
    )
    page = context.new_page()
    page.goto(BASE + "/", wait_until="networkidle")
    page.get_by_role("button", name="家庭Aで始める").click()
    to_parts(page)
    code = page.evaluate("location.hash.slice(1)")
    page.locator("[data-save-link]").get_by_role("button", name="あとで").click()

    def plan_with(branch_id: str, draft: str) -> dict:
        version = request("GET", "/api/board", code)["version"]
        return request(
            "POST",
            "/api/board/branches",
            code,
            {
                "version": version,
                "branch": {
                    "id": branch_id,
                    "condition_item_id": "a-03",
                    "candidate_days": [],
                    "outcomes": [
                        {
                            "label": "そのとき",
                            "actions": [
                                {
                                    "part": "職場",
                                    "type": "伝える",
                                    "draft": draft,
                                    "basis_ids": ["a-03"],
                                    "release": [],
                                }
                            ],
                        }
                    ],
                },
            },
        )

    old_id = plan_with("x", "前の案の連絡です")["items"][-1]["id"]
    new_id = plan_with("y", "新しい案の連絡です")["items"][-1]["id"]
    page.reload(wait_until="networkidle")
    older = page.locator(f"[data-item='{old_id}']")
    older.wait_for()
    report.check("前の案" in older.inner_text(), "前の案の印が無い")
    report.check(
        "前の案" not in page.locator(f"[data-item='{new_id}']").inner_text(), "新しい文に印"
    )
    inspect(page, "20g-older-proposal", out, report)
    to_now(page)
    top = page.locator("[data-next-step]").inner_text()
    report.check("ほか" not in top and "下書きを見る" in top, f"前の案の文まで数えている: {top}")
    # 新しい文を送り、ほかの用事も終えると、上段は残った前の案の文のことを言う。
    # 前の案の文は開けて、送れて、消せる。
    for item in request("GET", "/api/board", code)["items"]:
        if item["id"] == old_id or item["kind"] not in ("行動", "質問"):
            continue
        status = (
            "頼んだ" if item["id"] == new_id else "完了" if item["kind"] == "行動" else "答えあり"
        )
        version = request("GET", "/api/board", code)["version"]
        request(
            "PATCH", f"/api/board/items/{item['id']}", code, {"version": version, "status": status}
        )
    version = request("GET", "/api/board", code)["version"]
    request(
        "PATCH", f"/api/board/items/{new_id}", code, {"version": version, "status": "引き受けた"}
    )
    version = request("GET", "/api/board", code)["version"]
    request("PATCH", f"/api/board/items/{new_id}", code, {"version": version, "status": "完了"})
    page.reload(wait_until="networkidle")
    top = page.locator("[data-next-step]").inner_text()
    report.check(
        "前の案の下書きが1件残っています" in top, f"前の案の文が残っていると言わない: {top}"
    )
    report.check("下書きを見る" not in top, "前の案の文だけなのに下書きのボタンが出る")
    to_parts(page)
    older.locator(".row-lead.send").click()
    report.check("まだ送っていません" in older.inner_text(), "前の案の文の説明が無い")
    report.check(
        older.get_by_role("button", name="送ったと記録").count() == 1, "前の案の文を送れない"
    )
    report.check(older.get_by_role("button", name="消す").count() == 1, "前の案の文を消せない")
    inspect(page, "20h-older-open", out, report)

    # 前の案の文が医療の問いになっても、これからの用件の数を減らさない（QA TestのW1）。
    board = request("GET", "/api/board", code)
    other = next(
        i for i in board["items"] if i["kind"] == "行動" and i["id"] not in (old_id, new_id)
    )
    request(
        "PATCH",
        f"/api/board/items/{other['id']}",
        code,
        {"version": board["version"], "status": "頼みたい"},
    )
    version = request("GET", "/api/board", code)["version"]
    request(
        "PATCH",
        f"/api/board/items/{old_id}/content",
        code,
        {"version": version, "task_summary": "熱が下がらないので聞きたい"},
    )
    after = next(i for i in request("GET", "/api/board", code)["items"] if i["id"] == old_id)
    report.check(after["medical"] == "ask", "前の案の文が医療の問いにならなかった")
    page.reload(wait_until="networkidle")
    to_now(page)
    text = page.locator("[data-next-step]").inner_text()
    report.check(MEDICAL_FIXED_TEXT in text, f"医療の問いになった文の固定文が出ない: {text}")
    context.close()


def withdraw_basis(page: Page, code: str, out: Path, report: Report) -> None:
    """案の根拠の項目も消せて、取り下げを先に知らせる。ゴミ箱から案ごと戻る。"""
    before = request("GET", "/api/board", code)
    unsent_family = [
        i["id"]
        for i in before["items"]
        if i["id"].startswith(f"s-{before['branches'][-1]['id']}") and i["part"] == "保育園・預け先"
    ]
    basis = show_row(page, "a-02")
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
    open_trash(page)
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
    to_parts(page)


def photo_store(browser, page: Page, code: str, out: Path, report: Report) -> None:
    """写真はこの端末の中だけ。サーバーにもAIにも送らない。"""
    picture = {"name": "test.png", "mimeType": "image/png", "buffer": synthetic_png()}
    sent: list = []

    def watch(request_) -> None:
        sent.append(request_)

    page.on("request", watch)

    # 写真だけ：AIを呼ばず、保管庫に入る。
    open_add(page)
    submit = page.get_by_role("button", name="整理する", exact=True)
    report.check(submit.is_disabled(), "文も写真も無いのに追加できる")
    page.locator("input[type=file]").set_input_files([picture, picture])
    page.get_by_text("この端末の中だけに保存します").wait_for()
    report.check(page.locator(".photo-chosen").count() == 2, "選んだ写真の数が違う")
    inspect(page, "33-add-photo", out, report)
    page.get_by_role("button", name="2枚目の写真を外す").click()
    report.check(page.locator(".photo-chosen").count() == 1, "写真を外せない")
    page.get_by_role("button", name="検査結果", exact=True).click()
    submit.click()
    page.get_by_text("写真を保存しました").wait_for()
    report.check(not any("/intake" in r.url for r in sent), "写真だけなのに仕分けを呼んだ")

    # 写真と文：文だけをAIへ送り、仕分けた項目に写真が付く。
    open_add(page)
    page.get_by_role("textbox").fill(SORTED_TEXT)
    page.locator("input[type=file]").set_input_files([picture])
    page.get_by_role("button", name="整理する", exact=True).click()
    to_parts(page)
    card = page.locator("[data-item]", has_text=SORTED_TEXT).first
    reveal(card)
    card.get_by_text("写真1枚").wait_for()
    card.locator(".row-main").click()
    card.locator(".photo-thumb").wait_for()
    inspect(page, "34-board-photo", out, report)
    card.locator(".photo-thumb").click()
    page.get_by_text("一緒に入れた文").wait_for()
    report.check(SORTED_TEXT in page.locator("main").inner_text(), "写真の画面に文が出ない")
    inspect(page, "35-photo-viewer", out, report)
    page.get_by_role("button", name="戻る").click()

    # 保管庫：履歴と、種類ごとの棚。
    open_vault(page)
    tiles = page.locator(".photo-tile")
    tiles.first.wait_for()
    report.check(tiles.count() == 2, f"保管庫の写真が{tiles.count()}枚")
    inspect(page, "36-vault", out, report)
    page.get_by_role("button", name="検査結果", exact=True).click()
    report.check(tiles.count() == 1, "検査結果の棚の枚数が違う")
    page.get_by_role("button", name="入院のしおり", exact=True).click()
    page.get_by_text("まだ資料がありません").wait_for()
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
    open_trash(page)
    deleted_photo = page.locator("[data-trash-photo]")
    deleted_photo.wait_for()
    inspect(page, "36a-trash-photo", out, report)
    deleted_photo.get_by_role("button", name="元に戻す").click()
    page.get_by_text("元に戻しました").wait_for()
    page.get_by_role("button", name="戻る").click()
    open_vault(page)
    tiles.first.wait_for()
    report.check(tiles.count() == 2, "戻した写真が保管庫に無い")
    tiles.first.click()
    page.get_by_role("button", name="消す").click()
    page.get_by_role("button", name="もう一度押すと、ゴミ箱に入れます").click()
    tiles.first.wait_for()
    page.get_by_role("button", name="戻る").click()
    open_trash(page)
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
    to_parts(b_page)
    open_add(b_page)
    b_page.locator("input[type=file]").set_input_files([picture])
    b_page.get_by_role("button", name="整理する", exact=True).click()
    b_page.get_by_text("このブラウザでは写真を保存できません").wait_for()
    inspect(b_page, "37-add-photo-blocked", out, report)
    b_page.get_by_role("button", name="1枚目の写真を外す").click()
    b_page.get_by_role("textbox").fill("売店で買う")
    b_page.get_by_role("button", name="整理する", exact=True).click()
    to_parts(b_page)
    open_vault(b_page)
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
        open_add(page)
        page.get_by_role("button", name="聞きたい").click()
        page.get_by_role("button", name="医師・看護師").click()
        page.get_by_role("textbox").fill(text)
        page.get_by_role("button", name="整理する", exact=True).click()
        to_parts(page)

    page.on("request", watch)

    # 検査の結果には、読ませる選択が出ない。選ばなければ何も送らない。
    open_add(page)
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
    page.get_by_role("button", name="整理する", exact=True).click()
    to_parts(page)
    page.get_by_text("写真を保存しました").wait_for()
    report.check(reads() == [], "選んでいないのに写真を送った")

    # 読ませると選んだ写真だけを送る。
    open_add(page)
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
    page.get_by_role("button", name="整理する", exact=True).click()
    to_parts(page)
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
    other.get_by_text("写真の中には見つかりませんでした").wait_for()
    inspect(page, "42-board-not-found", out, report)

    # 医療の問いは探さず、固定文だけ。
    before = len(searches())
    add_text(page, "薬を増やしたほうがいいですか")
    asked = page.locator("[data-item]", has_text="薬を増やしたほうがいいですか").first
    asked.wait_for()
    page.wait_for_timeout(2500)
    report.check(MEDICAL_FIXED_TEXT in asked.inner_text(), "医療の問いに固定文が出ない")
    report.check(len(searches()) == before, "医療の問いで保管庫を探した")
    report.check(
        asked.get_by_role("button", name="写真から探す").count() == 0, "探すボタンが出ている"
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
    early.get_by_role("button", name="写真から探す").click()
    early.get_by_text("入院のしおりにありました").wait_for()

    # 検査結果へ変えたら、読み取った文を消して、探す対象から外す。
    open_vault(page)
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
    to_parts(page)
    page.wait_for_timeout(500)
    report.check(
        page.get_by_role("button", name="写真から探す").count() == 0,
        "読み取った資料が無いのに探すボタンが出ている",
    )

    # あとから読ませることもできる。
    open_vault(page)
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
    open_trash(page)
    page.get_by_text("ゴミ箱は空です").wait_for()
    inspect(page, "31a-trash-empty", out, report)
    page.get_by_role("button", name="戻る").click()
    # 写真もボードと一緒に消える。
    picture = {"name": "test.png", "mimeType": "image/png", "buffer": synthetic_png()}
    page.evaluate("localStorage.setItem('ensemble.consented', 'true')")
    page.reload(wait_until="networkidle")
    open_add(page)
    page.locator("input[type=file]").set_input_files([picture])
    page.get_by_role("button", name="整理する", exact=True).click()
    to_parts(page)
    page.get_by_text("写真を保存しました").wait_for()
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
    to_parts(other_page)

    open_consent(page)
    page.get_by_role("button", name="この記録を消す").wait_for()
    inspect(page, "32-consent-remove", out, report)
    page.get_by_role("button", name="この記録を消す").click()
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
    other_page.get_by_text("記録が見つかりません").wait_for(timeout=5000)
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
                    next_step_order(browser, args.out, report)
                    dock_undo(browser, args.out, report)
                    top_and_rows(browser, args.out, report)
                    lanes(browser, args.out, report)
                    from_link(browser, args.out, report)
                    older_proposal(browser, args.out, report)
                    older_family(browser, args.out, report)
                    tab_position(browser, args.out, report)
                    strip_frame(browser, args.out, report)
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
                    to_parts(dark_page)
                    inspect(dark_page, "10-board-dark", args.out, report)
                    dark_page.evaluate("localStorage.setItem('ensemble.consented', 'false')")
                    open_add(dark_page)
                    inspect(dark_page, "11-consent-dark", args.out, report)
                    dark_page.get_by_role("button", name="わかった").click()
                    inspect(dark_page, "12-add-dark", args.out, report)
                    dark_page.get_by_role("button", name="戻る").click()
                    add_text(dark_page, "今日の採血はだめでした")
                    plan_button(dark_page).click()
                    dark_page.get_by_role("button", name="下書きを作る").wait_for()
                    inspect(dark_page, "20-decide-dark", args.out, report)
                    # 案を進めて、その場で順に送る。送ったら次の相手へ進み、件数が減る。
                    dark.grant_permissions(["clipboard-read", "clipboard-write"], origin=BASE)
                    dark_page.get_by_role("button", name="下書きを作る").click()
                    dark_page.locator(".map-node").first.wait_for()
                    dark_page.get_by_text("下書きが3件あります").wait_for()
                    dark_page.wait_for_timeout(6000)
                    inspect(dark_page, "20a-map-dark", args.out, report)
                    dark_page.get_by_role("button", name="下書きを見る（3件）").click()
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
                    dark_page.get_by_role("button", name="送ったと記録").click()
                    dark_page.get_by_role("heading", name="職場に伝える").wait_for()
                    dark_page.get_by_role("button", name="戻る").click()
                    dark_page.get_by_text("下書きが2件あります").wait_for()
                    report.check(
                        "返事待ち" in dark_page.locator(".map-node[data-part='家族']").inner_text(),
                        "送った相手の状態が変わらない",
                    )
                    withdraw_basis(page, code, args.out, report)
                    photo_store(browser, page, code, args.out, report)
                    reading_flow(page, code, args.out, report)

                    # 撮影の初期状態：前のボードを開かず、医師の言葉も案も入っていない。
                    shoot = browser.new_context(
                        viewport={"width": WIDTH, "height": HEIGHT},
                        device_scale_factor=2,
                        color_scheme="light",
                    )
                    shoot_page = shoot.new_page()
                    shoot_page.goto(BASE + "/", wait_until="networkidle")
                    report.check(
                        shoot_page.get_by_role("button", name="撮影の初期状態で始める").count()
                        == 0,
                        "普段の画面に撮影の入口が出ている",
                    )
                    shoot_page.get_by_role("button", name="家庭Aで始める").click()
                    to_parts(shoot_page)
                    first_code = shoot_page.evaluate("location.hash.slice(1)")
                    shoot_page.goto(BASE + "/?shoot", wait_until="networkidle")
                    inspect(shoot_page, "1a-start-shoot", args.out, report)
                    shoot_page.get_by_role("button", name="撮影の初期状態で始める").click()
                    to_parts(shoot_page)
                    shoot_code = shoot_page.evaluate("location.hash.slice(1)")
                    report.check(shoot_code != first_code, "前のボードが開いた")
                    shoot_board = request("GET", "/api/board", shoot_code)
                    report.check(shoot_board["branches"] == [], "撮影の初期状態に案がある")
                    report.check(len(shoot_board["items"]) == 5, "撮影の初期状態の項目数が違う")
                    report.check(
                        "先生" not in str(shoot_board["items"]), "撮影の初期状態に医師の言葉がある"
                    )
                    inspect(shoot_page, "1b-board-shoot", args.out, report)
                    from_menu(shoot_page, "自分のこと")
                    report.check(
                        "今日はまだ何も食べられていない"
                        in shoot_page.locator("[data-part='親']").inner_text(),
                        "自分のことの一言が見えない",
                    )

                    # 消えたボードを開いたとき。
                    home_b = browser.new_context(
                        viewport={"width": WIDTH, "height": HEIGHT},
                        device_scale_factor=2,
                        color_scheme="light",
                    )
                    b_page = home_b.new_page()
                    b_page.goto(BASE + "/", wait_until="networkidle")
                    b_page.get_by_role("button", name="家庭Bで始める").click()
                    to_parts(b_page)
                    open_parts(b_page)
                    b_page.get_by_role("heading", name="支援").wait_for()
                    from_menu(b_page, "あなたとご家族のこと")
                    b_page.locator("[data-home-cards] .card").first.wait_for()
                    report.check(
                        "18:30" in b_page.locator("body").text_content(),
                        "家庭Bの延長保育の時刻が出ていない",
                    )
                    b_page.get_by_role("button", name="戻る", exact=True).click()
                    close_menu(b_page)
                    to_parts(b_page)
                    inspect(b_page, "14-board-home-b", args.out, report)
                    # 長い文でも、横いっぱいのタイルからはみ出さない。
                    b_code = b_page.evaluate("location.hash.slice(1)")
                    request(
                        "POST",
                        "/api/board/items",
                        b_code,
                        {
                            "version": request("GET", "/api/board", b_code)["version"],
                            "kind": "行動",
                            "part": "支援",
                            "certainty": "見込み",
                            "text_original": "まだ確定ではないため、決まりしだいご連絡します。"
                            "火曜の18:30以降に迎えをお願いできる空きがあるか教えてください",
                            "speaker": "親",
                            "status": "頼んだ",
                        },
                    )
                    b_page.reload(wait_until="networkidle")
                    to_parts(b_page)
                    inspect(b_page, "14a-board-long-support", args.out, report)
                    long_release(b_page, b_code, args.out, report)
                    outdated_note(browser, b_code, args.out, report)
                    removed_code = remove_board(browser, b_page, args.out, report)

                    gone = browser.new_context(viewport={"width": WIDTH, "height": HEIGHT})
                    gone_page = gone.new_page()
                    gone_page.goto(f"{BASE}/b#{'x' * 43}", wait_until="networkidle")
                    gone_page.get_by_text("記録が見つかりません").wait_for()
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
