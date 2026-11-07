"""画面の合格の検査。ビルドして手元でサーバーを立て、幅360pxで実際に操作して確かめる。

外へは何も送らない。Claude APIも呼ばない（scripts/fake_ai_app.py の偽の応答を使う）。
DBは一時フォルダの SQLite。
使い方: python -X utf8 scripts/check_screens.py <写真の出力フォルダ> [--no-build]
"""

import argparse
import json
import os
import re
from datetime import date, datetime, timedelta
import shutil
import socket
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
# 本文は14px（2026-11-07 PO が 15px から下げた）。小さい文字も13pxまで。
MIN_TAP, MIN_GAP, MIN_FONT, MIN_CONTRAST = 48, 8, 13, 4.5
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
            primary: el.hasAttribute('data-primary'), docked: !!el.closest('.dock, .bottom-nav, .app-bar, .week-nav, .add-fab'), day: !!el.closest('.cal-grid'),
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
        if reach and rect["primary"] and rect["top"] < HEIGHT:
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


def now_text(page: Page) -> str:
    """「いま」の次にできることの文と、下の帯のボタンの語。"""
    actions = page.locator("[data-now-actions]")
    tail = actions.inner_text() if actions.count() else ""
    return page.locator("[data-next-step]").inner_text() + "\n" + tail


# 夫の1つ目の答え（ありかのある物なら「持っていく」、ほかは「引き受ける」）。
FIRST_ANSWER = re.compile(r"^(持っていく|引き受ける)$")


def first_answer(page: Page):
    return page.get_by_role("button", name=FIRST_ANSWER)


def reply_one(page: Page, choice: str, note: str | None = None) -> None:
    """夫の画面で返事を選んで送る。「持っていく」は1つ目の答え（引き受ける）も指す。"""
    if choice == "持っていく":
        first_answer(page).click()
    else:
        page.get_by_role("button", name=choice, exact=True).click()
    if note:
        page.get_by_role("textbox", name="ひとこと").fill(note)
    page.get_by_role("button", name="この返事を送る").click()


def to_now(page: Page) -> None:
    nav(page, "いま")
    page.locator("[data-next-step]").wait_for()


def to_parts(page: Page) -> None:
    """メッセージ（相手ごと）。2026-11-07 PO：やりとりは「メッセージ」のタブの中。
    2026-11-08 タスク管理：メッセージは「やること」から開くので、切り替えで相手ごとへ。"""
    nav(page, "やること")
    switch = page.locator("[data-messages-switch]")
    switch.wait_for()
    switch.get_by_role("tab", name="相手ごと").click()
    page.locator("[data-part='職場']").wait_for()


def to_progress(page: Page) -> None:
    """メッセージの中の「やること」（前の「1件ずつ」の作り替え、2026-11-08）。"""
    nav(page, "やること")
    page.locator("[data-messages-switch]").get_by_role("tab", name="一覧").click()
    page.locator("[data-lanes]").wait_for()


def tab(page: Page, name: str) -> None:
    """やりとり、進み具合、カレンダーを開く。"""
    if name == "相手別":
        to_parts(page)
    elif name == "進捗別":
        to_progress(page)
    else:
        nav(page, "カレンダー")
        page.locator(".week-view").wait_for()


def plan_button(page: Page):
    """［変わるところを見る］は「いま」にある。"""
    to_now(page)
    return page.get_by_role("button", name="変わるところを見る")


def quit_plan(page: Page) -> None:
    """［この案をやめる］は、図の⑥では「案の中身を見る」の先にある。"""
    if page.locator("[data-decide-summary]").count():
        page.locator("[data-decide-detail]").click()
    page.get_by_role("button", name="この案をやめる").click()


def plan_detail(page: Page) -> None:
    """図の⑥で、根拠と線の図・候補日の行（今回選んだ内容）を開く。"""
    if page.locator("[data-decide-summary]").count():
        page.locator("[data-decide-detail]").click()
        page.locator("[data-decide-back]").wait_for()


def edit_plan(page: Page) -> None:
    """文を直す一覧（相手ごとに送る・送らない）。図の⑥では［内容を直す］。"""
    if page.locator("[data-decide-summary]").count():
        page.locator("[data-decide-edit]").click()
    else:
        page.get_by_role("button", name="文を確かめて直す").click()


def open_add(page: Page) -> None:
    to_now(page)
    page.locator("[data-now-input]").click()


def dismiss_save_link(page: Page) -> None:
    """URLの保存の1行を開いて［あとで］を押す（2026-11-07 PO：1行は「いま」だけに出て、押すとカード）。

    ほかのタブにいるときは「いま」へ行って閉じ、元のタブへ戻る。
    """
    line = page.locator("[data-save-link-line]")
    back = None
    if line.count() == 0:
        back = page.locator(".bottom-nav [aria-current='page']").inner_text().strip()
        nav(page, "いま")
        line.wait_for()
    line.click()
    page.locator("[data-save-link]").get_by_role("button", name="あとで").click()
    if back and back != "いま":
        nav(page, back)
        if back == "やること":
            page.locator("[data-part='職場']").wait_for()


def skip_candidate_check(page: Page, code: str) -> None:
    """作り手ルール②の確認を一度押した状態にする（ほかの次にできることの順を見る場面のため）。"""
    doctor = request("GET", "/api/board", code).get("doctor")
    if doctor:
        key = f"{doctor['item_id']}@{doctor['recorded_at']}"
        page.evaluate(
            "(key) => localStorage.setItem('ensemble.candidateChecked', JSON.stringify([key]))", key
        )
        page.reload(wait_until="networkidle")


def start_home(page: Page, name: str) -> None:
    """図の①：家庭Aで試す・家庭Bで試すは「今ある記録から始める」の下に、畳まずに出る（4ecb678）。"""
    page.get_by_role("button", name=name, exact=True).click()


ABOUT_ROWS = ("使い方", "同意と線引き", "家庭A・Bで試す")


def from_menu(page: Page, name: str) -> None:
    """2026-11-07 PO：設定の行は下のナビの「設定」、使い方・同意・試す家庭はハンバーガー。"""
    if name == "しおり・資料から探す":
        nav(page, "さがす")
        return
    if name in ABOUT_ROWS:
        page.get_by_role("button", name="メニュー", exact=True).click()
        page.locator("[data-menu]").wait_for()
        page.get_by_role("button", name=name, exact=True).click()
        return
    nav(page, "設定")
    page.locator("[data-settings]").wait_for()
    target = page.get_by_role("button", name=name, exact=True)
    if target.count() == 0:
        # 図の⑫：ゴミ箱・相手ごとは「これまでに入れたもの」の中の段にある。
        page.get_by_role("button", name="これまでに入れたもの", exact=True).click()
    target.click()


def unfold_vault_list(page: Page) -> None:
    """図の④：資料の写真の一覧は「資料を追加する」の下に畳んである。読み直すと畳まれる。"""
    listing = page.locator("[data-search-list]")
    listing.wait_for()
    if listing.get_attribute("open") is None:
        listing.locator("summary").first.click()


def open_vault_list(page: Page) -> None:
    open_vault(page)
    unfold_vault_list(page)


def open_trash(page: Page) -> None:
    from_menu(page, "ゴミ箱")


def open_consent(page: Page) -> None:
    from_menu(page, "データの扱いと、記録を消す")


def open_vault(page: Page) -> None:
    from_menu(page, "しおり・資料から探す")
    page.locator(".vault").wait_for()


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


def press_accept(page: Page) -> None:
    page.get_by_role("button", name=re.compile("送る文を見る・")).click()


def accept_plan(page: Page) -> None:
    """案を進める。今までの場面は地図の中身を確かめるので、地図まで開く。"""
    press_accept(page)
    page.locator(".board").wait_for()
    open_map(page)
    page.locator(".map-node").first.wait_for()


def back_from_map(page: Page) -> None:
    page.get_by_role("button", name="いまへ戻る").click()
    to_parts(page)


def close_menu(page: Page) -> None:
    """ハンバーガーを閉じる。設定のタブは下のナビのままなので、閉じるものが無い。"""
    close = page.get_by_role("button", name="メニューを閉じる")
    if close.count():
        close.click()


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
    """次にやることの画面へ。「いま」の［決めた内容を見る］から。"""
    to_now(page)
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
    const counted = [...head.matchAll(/(?:未送信|これから|保留|返事待ち|対応中|引き受けた)(\\d+)/g)].reduce((sum, m) => sum + Number(m[1]), 0);
    const rows = [...section.querySelectorAll(':scope > .rows > .row:not(.own)')]
      .filter((row) => !row.querySelector('.medical') || row.querySelector('.chip.state')).length;
    if (counted !== rows) found.push(`${section.dataset.part}: 数${counted} 行${rows}`);
  }
  return found;
}"""


# 2026-11-07 家庭Bの文を作る相手は家族と職場だけになった（保育園への案はサーバーが落とす）ので、職場で見る。
LONG_SENT = "お世話になります。 木曜の会議の扱いを確認させてください。 決まりしだいご連絡します。"
LONG_SENT_SHORT = "木曜の会議の扱いを確認させてください"


def long_release(page: Page, code: str, out: Path, report: Report) -> None:
    """送った連絡を後の案で取り消しても、短い1行で出る。全文は「詳しく」で読める。"""
    sent = request(
        "POST",
        "/api/board/items",
        code,
        {
            "version": request("GET", "/api/board", code)["version"],
            "kind": "行動",
            "part": "職場",
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
                                "part": "職場",
                                "type": "確認する",
                                "draft": "火曜の会議の扱いを確認させてください",
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
    node = page.locator(".map-node[data-part='職場']")
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
    made = next(i for i in accepted["items"] if (i["text_original"] or "").startswith("火曜の会議"))
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
    report.check(first_answer(page).count() == 1, "古い連絡に返事ができない")
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
    report.check(first_answer(page).count() == 0, "取り消した依頼に4択が出ている")
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
    first_answer(page).wait_for()
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

    start_home(page, "家庭Aで試す")
    to_parts(page)
    code = page.evaluate("location.hash.slice(1)")
    report.check(len(code) >= 32, "家族コードがURLの # より後ろに無い")
    report.check(code not in page.evaluate("location.pathname"), "家族コードがパスに入っている")
    inspect(page, "2-board", out, report)
    # 作った直後に、URLを自分へ送る小さい1行が出る（2026-11-07 PO）。押すと案内が開く。
    # 閉じたら開き直しても出ない。
    save_line = page.locator("[data-save-link-line]")
    save_link = page.locator("[data-save-link]")
    # 1行は「いま」だけ（UX Design の P1：ほかのタブに余白を作らない）。
    report.check(save_line.count() == 0, "いま以外のタブにURLの1行が出ている")
    to_now(page)
    report.check(save_line.count() == 1, "URLを送る1行が出ていない")
    report.check(save_link.count() == 0, "押す前から案内のカードが出ている")
    save_line.click()
    report.check(save_link.count() == 1, "1行を押しても案内が出ない")
    save_link.get_by_role("button", name="あとで").click()
    page.reload(wait_until="networkidle")
    to_now(page)
    report.check(save_line.count() == 0 and save_link.count() == 0, "閉じた案内がまた出た")
    to_parts(page)
    # 用件を開いたまま見せるので、畳んでいた頃の2画面より少しだけ増やす。
    within_height(page, "2-board", 2.2, report)
    # この家の事情は畳んである。開くと3枚。
    from_menu(page, "あなたと家族のこと")
    home_cards = page.locator("[data-home-cards]")
    home_cards.locator(".card").first.wait_for()
    report.check(home_cards.locator(".card").count() == 3, "この家の事情が3枚でない")
    report.check("夫（" in home_cards.inner_text(), "用事を頼める人が出ていない")
    # 試す家庭も、そのボードだけ直せる（2026-11-07 PO）。カードの下に［直す］。
    report.check(page.locator("[data-edit-home]").count() == 1, "試す家庭に［直す］が無い")
    inspect(page, "2c-board-home", out, report)
    page.get_by_role("button", name="戻る", exact=True).click()
    close_menu(page)
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
    for text in ("いま", "やること", "相手ごと", "病院", "家族", "保育園・預け先", "職場",
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
        page.locator("[data-column]").evaluate_all("els => els.map((el) => el.dataset.column)")[:2]
        == ["未送信", "引き受けた"],
        "進み具合の上の1行の順が違う",
    )
    inspect(page, "2a-board-progress", out, report)
    fixed_note(page, "進捗別", report)

    # 週の予定：8日の帯と日ごとの一覧。文は切らない。行を押すと相手別のその行へ。
    tab(page, "週の予定")
    inspect(page, "2b-board-week", out, report)
    days = page.locator("[data-cal-day]")
    report.check(days.count() == 14, f"カレンダーが{days.count()}日")
    report.check(page.locator(".cal-day.today").count() == 1, "カレンダーに今日の印が無い")
    heads = page.locator(".cal-head span").all_inner_texts()
    report.check(heads[0] == "月" and heads[-1] == "日", f"月曜始まりでない: {heads}")
    # 日を押すと、その日の予定が原文で読める。全部の日を見て確かめる。
    seen = []
    lines: list[str] = []
    plans = 0
    for index in range(days.count()):
        days.nth(index).click()
        page.wait_for_timeout(30)
        seen.append(page.locator("[data-week-selected]").inner_text())
        lines += page.locator("[data-week-selected] .week-row-status").all_inner_texts()
        plans += page.locator("[data-week-selected] .week-row").count()
    # 日付が決まっていない予定の行も同じ（QA Test の指摘）。
    lines += page.locator("[data-undated] .week-row-status").all_inner_texts()
    plans += page.locator("[data-undated] .week-row").count()
    # 予定の行は、状態の有無にかかわらず2段目の終わりに時刻（M/D H:MM）を出す。
    report.check(
        plans > 0
        and len(lines) == plans
        and all(re.search(r"\d{1,2}/\d{1,2} \d{1,2}:\d{2}$", text) for text in lines),
        f"⑩の行に時刻が無い: {plans}行 {lines}",
    )
    # 時刻だけの行は、予定の時刻に読める。返事の札（「夫：」）の行のほかは、出来事の語を前に付ける。
    report.check(
        all(
            re.search(r"(^|・)\S+ \d{1,2}/\d{1,2} \d{1,2}:\d{2}$", text) or "：" in text
            for text in lines
        )
        and not any(re.fullmatch(r"\d{1,2}/\d{1,2} \d{1,2}:\d{2}", text) for text in lines),
        f"⑩の時刻に出来事の語が無い: {lines}",
    )
    week_text = " ".join(seen)
    report.check("明日の採血しだい" in week_text, "カレンダーに医師の言葉が無い")
    report.check("木曜の会議" in week_text, "カレンダーに職場の予定が無い")
    report.check(page.locator(".week-view table").count() == 0, "表が残っている")
    # マスに長い文を出さない。
    marks = page.locator(".cal-mark").all_inner_texts()
    report.check(all(len(mark) <= 3 for mark in marks), f"マスの語が長い: {marks}")
    # 次の週へ動ける。
    page.get_by_role("button", name="次の週").click()
    report.check(days.count() == 14, "次の週でマスの数が変わる")
    page.get_by_role("button", name="前の週").click()
    page.locator("[data-cal-day].today").click()
    for index in range(days.count()):
        if days.nth(index).locator(".cal-dot, .cal-mark").count():
            days.nth(index).click()
            if page.locator("[data-week-selected] .week-row").count():
                break
    plan_row = page.locator(".week-row").first
    plan_id = plan_row.get_attribute("data-week-item")
    plan_row.click()
    opened = page.locator(f"[data-item='{plan_id}']")
    opened.wait_for()
    report.check(
        page.locator(".bottom-nav [aria-current='page']").inner_text().strip() == "やること",
        "週の行を押してもメッセージへ行かない",
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
        report.check("日付が決まっていない予定" in undated.inner_text(), "日付のない予定の欄が無い")
        report.check(
            MEDICAL_FIXED_TEXT not in undated.inner_text(), "日付のない用事に医療の問いが出ている"
        )
    to_now(page)
    report.check(page.locator("[data-map-open]").count() == 0, "案が無いのに地図の入口が出ている")

    # 入れる（図の②）。同意の3行は、AIへ送る最初の［整理する］で1回だけ出る。
    open_add(page)
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
    page.get_by_role("button", name="わかった").wait_for()
    inspect(page, "3-consent", out, report)
    page.get_by_role("button", name="わかった").click()
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

    # もう1回押しても同意は出ない。②は見出しの［戻る］でなく［あとで続ける］で抜ける。
    open_add(page)
    report.check(page.get_by_role("textbox").count() == 1, "同意が2回出る")
    report.check(page.get_by_role("textbox").input_value() == "", "入れた後に文が残っている")
    page.locator("[data-input-later]").click()

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

    # 図の②：選ぶ札は無く、入れて整理するだけ。写真・音声・貼り付けの3つ。キーボードで狭くても押せる。
    open_add(page)
    for marker in ("[data-input-photo]", "[data-input-voice]", "[data-input-paste]"):
        report.check(page.locator(marker).count() == 1, f"②に{marker}が無い")
    report.check(page.get_by_text("誰に").count() == 0, "図に無い相手の欄が出ている")
    # 音声：キーボードのマイクが使えないとき（シークレットモードなど）の道も書く（2026-11-08 夫の初見）。
    page.locator("[data-input-voice]").click()
    report.check(
        "文字か写真で" in page.locator("[data-voice-hint]").inner_text(),
        "音声が使えないときの案内が無い",
    )
    inspect(page, "15a-add-choices", out, report)
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
    reveal(page.locator("[data-item]", has_text="着替えの場所を教えて").first)
    report.check(
        any(
            item["text_original"] == "着替えの場所を教えて"
            for item in request("GET", "/api/board", code)["items"]
        ),
        "入れた文が台帳に無い",
    )
    # 入れた後に開き直しても、前の文は残らない。②は［あとで続ける］で抜ける。
    open_add(page)
    report.check(page.get_by_role("textbox").input_value() == "", "前の文が残っている")
    page.locator("[data-input-later]").click()

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
    quit_plan(page)
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
    quit_plan(page)
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
    quit_plan(page)
    page.get_by_role("heading", name="次にできること").wait_for()

    # 応答が返らないままでも、戻れば進む。
    page.route("**/api/board/proposal", lambda route: None)
    add_text(page, "今日の採血はだめでした")
    page.get_by_text("案を作っています").first.wait_for()
    page.unroute("**/api/board/proposal")
    back_to_app()
    plan_button(page).wait_for()
    plan_button(page).click()
    quit_plan(page)
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
    quit_plan(page)
    page.get_by_role("heading", name="次にできること").wait_for()

    # 1つの問い：［この案をやめる］は台帳を変えない。
    add_text(page, "今日の採血はだめでした")
    page.get_by_text("案を作っています").first.wait_for()
    plan_button(page).wait_for()
    before = request("GET", "/api/board", code)
    plan_button(page).click()
    page.get_by_role("button", name=re.compile("送る文を見る・")).wait_for()
    inspect(page, "17-decide", out, report)
    fixed_note(page, "いま決めること", report)
    # 要約だけ。札は出さず、下書きの全文は送る段で読む。
    report.check(page.locator(".plan-toggle").count() == 0, "普段から切り替えが押せる")
    report.check(page.locator(".badge").count() == 0, "普段から札が出ている")
    summary_text = page.locator("main").inner_text()
    # 図の⑥：見直しの黄の枠、用件ごとのチェック、件数。下書きの全文は送る段で読む。
    report.check("3件を、3人分の文にまとめます" in summary_text, "件数の要約が無い")
    rows = page.locator("[data-plan-row]")
    report.check(rows.count() == 3, f"用件の行が{rows.count()}行")
    report.check(
        all(row.get_attribute("aria-checked") == "true" for row in rows.all()),
        "用件の行が最初から外れている",
    )
    report.check("着替えを追加で持ってきてほしい" in summary_text, "夫への用件が無い")
    report.check("お疲れさまです" not in summary_text, "下書きの全文が出ている")
    report.check("変える" not in summary_text, "作り手の言葉が出ている")
    review = page.locator("[data-review-line]").all_inner_texts()
    report.check(
        any("退院の候補" in line and "→" in line for line in review), f"見直しが無い: {review}"
    )
    for text in ("変わるところだけ", "今回の記録から整理しました", "連絡・お願いの候補"):
        report.check(text in summary_text, f"変わるところだけに「{text}」が無い")
    # 線の図と根拠は「案の中身を見る」の先。
    page.locator("[data-decide-detail]").click()
    page.locator("[data-decide-back]").wait_for()
    body = page.locator("body").inner_text()
    for text in ("今日の採血はだめでした", "よければ木曜に帰れるかも", "取り消し", "候補",
                 "木の延長の空きを確認"):  # fmt: skip
        report.check(text in body, f"今回選んだ内容に「{text}」が無い")
    quit_plan(page)
    page.get_by_role("heading", name="次にできること").wait_for()
    report.check(request("GET", "/api/board", code) == before, "何もしないで台帳が変わった")

    # 開いて戻っても案は残り、一部を外して進められる。押す前は状態が変わらない。
    add_text(page, "今日の採血はだめでした")
    plan_button(page).wait_for()
    plan_button(page).click()
    page.locator(".decide-head .back").click()
    plan_button(page).click()
    edit_plan(page)
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
        page.get_by_role("button", name=re.compile("送る文を見る・")).count() == 1,
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
    press_accept(page)
    page.get_by_text("医療の語が入っている文は送れません").wait_for()
    edited_box.fill(original_draft + "土曜でもいいよ。")
    family_row.get_by_role("button", name="この文にする").click()
    inspect(page, "18c-decide-edited", out, report)
    pressed = request("GET", "/api/board", code)
    report.check(pressed["branches"] == [], "押す前に案が台帳に入った")
    accept_plan(page)
    # 次にやること：進めたら開く。データのある線だけ。カードには、見た人がすることを書く。
    page.get_by_text("木曜の退院候補を取り消し").wait_for()
    page.wait_for_timeout(6000)  # 線と帯が灯り終わるまで
    inspect(page, "18a-map", out, report)
    report.check(page.get_by_text("送る文が2件あります").count() == 1, "連絡の件数が出ていない")
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
        "夫に頼む" in family_node and "未送信" in family_node, f"やることが無い: {family_node}"
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
    # 開く曜日によっては、候補の火曜は帯の外の行に出る。
    strip = page.locator(".map-strip").inner_text()
    report.check("取り消し" in strip and "候補" in strip, f"帯に候補日が無い: {strip}")
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

    # ［送る文を見る］は最初の未送信の相手を開く。
    page.get_by_role("button", name="送る文を見る（2件）").click()
    page.get_by_role("heading", name="夫に頼む").wait_for()
    page.get_by_role("button", name="戻る").click()
    back_from_map(page)
    open_sends(page)
    inspect(page, "19-board-after", out, report)
    to_now(page)
    report.check(
        page.locator("[data-now-actions]")
        .get_by_role("button", name=re.compile("送る文を見る|人分を見る"))
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
    row.get_by_role("button", name="直す", exact=True).click()
    panel = row.locator("[data-edit]")
    panel.wait_for()
    report.check("作った案" in panel.inner_text(), "案は変わらないことが書いていない")
    inspect(page, "20d-edit", out, report)
    panel.get_by_label("担当").fill("母")
    panel.get_by_label("期限").fill("木曜の朝")
    panel.get_by_role("button", name="保存する").click()
    page.get_by_text("直しました").wait_for()
    row.get_by_text("担当：母").wait_for()
    row.get_by_text("期限：木曜の朝").wait_for()
    report.check(panel.count() == 0, "保存しても欄が閉じない")
    report.check("直した" in row.locator(".timeline").inner_text(), "直した時刻が履歴に無い")
    # 期日は週の表にも出る（台帳の同じ欄なので）。
    saved = next(i for i in request("GET", "/api/board", code)["items"] if i["id"] == "a-06")
    report.check(saved["due"] == "木曜の朝", "期日が台帳に入っていない")
    # 開いている間にもう1台が直した欄を、触っていなければ古い文で上書きしない。
    row.get_by_role("button", name="直す", exact=True).click()
    version = request("GET", "/api/board", code)["version"]
    request(
        "PATCH",
        "/api/board/items/a-06/content",
        code,
        {"version": version, "task_summary": "もう1台が直した用件"},
    )
    row.get_by_text("もう1台が直した用件").wait_for(timeout=8000)
    panel.get_by_label("期限").fill("金曜の朝")
    panel.get_by_role("button", name="保存する").click()
    row.get_by_text("期限：金曜の朝").wait_for()
    saved = next(i for i in request("GET", "/api/board", code)["items"] if i["id"] == "a-06")
    report.check(saved["task_summary"] == "もう1台が直した用件", "もう1台の変更が消えた")
    # 親以外の言葉は原文のまま残す。欄を出さず、理由を出す。
    doctor_item = next(
        i
        for i in request("GET", "/api/board", code)["items"]
        if i["text_original"] and i["speaker"] not in (None, "親")
    )
    doctor = show_row(page, doctor_item["id"])
    doctor.get_by_role("button", name="直す", exact=True).click()
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
    row.get_by_role("button", name="直す", exact=True).click()
    panel.get_by_label("担当").fill("別の人")
    panel.get_by_role("button", name="戻る").click()
    row.get_by_text("担当：母").wait_for()

    # 未送信の連絡は文を直せて、送る文も同じになる。医療の語は通さない。
    send_row = page.locator("[data-send='家族']").first
    send_id = send_row.get_attribute("data-item")
    if send_row.locator(".row-body").count() == 0:
        send_row.locator(".row-lead.send").click()
    send_row.get_by_role("button", name="直す", exact=True).click()
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
    page.locator(".dock").get_by_text("直しました").wait_for()
    send_row.get_by_text("よろしくね").wait_for()
    saved = next(i for i in request("GET", "/api/board", code)["items"] if i["id"] == send_id)
    report.check(saved["text_original"].endswith("よろしくね"), "送る文が直っていない")
    # 案から作った連絡は、相手を変えられない（欄を出さない）。
    send_row.get_by_role("button", name="直す", exact=True).click()
    report.check(send_row.get_by_label("相手").count() == 0, "連絡に相手の欄が出ている")
    send_row.get_by_role("button", name="戻る").click()
    # 検査の続きのために、元の文へ戻す。
    send_row.get_by_role("button", name="直す", exact=True).click()
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
    moved.get_by_role("button", name="直す", exact=True).click()
    moved.get_by_label("相手").select_option(target_part)
    moved.get_by_role("button", name="保存する").click()
    page.locator(f"[data-part='{target_part}'] [data-item='a-06']").wait_for()
    inspect(page, "20e-edit-moved", out, report)
    # 元へ戻す。
    back = show_row(page, "a-06")
    back.get_by_role("button", name="直す", exact=True).click()
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
        family_card.get_by_role("button", name="LINEなどで送る").count() == 0,
        "使えない共有が出ている",
    )
    report.check(
        "faint"
        in (
            family_card.get_by_role("button", name="送った", exact=True).get_attribute("class")
            or ""
        ),
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
    childcare_card.get_by_role("button", name="送った", exact=True).click()
    moved = page.locator(f"[data-item='{childcare_id}']")
    moved.get_by_text("返事待ち").wait_for()
    moved.get_by_text("送った").wait_for()
    page.get_by_text("送りました").wait_for()
    # 送った連絡の題は最初の用件の文。挨拶も全文も題にしない。全文は行を開くと読める。
    moved_title = moved.locator(".row-title").inner_text()
    report.check(
        "火曜の延長" in moved_title
        and "お世話" not in moved_title
        and "よろしく" not in moved_title,
        f"送った連絡の題が短くない: {moved_title}",
    )
    moved.locator(".row-main").click()
    report.check("送った文の全文" in moved.inner_text(), "送った連絡の全文が読めない")
    report.check("よろしくお願いします" in moved.inner_text(), "送った連絡の全文が読めない")
    # 履歴：文を作った→送った。送った後の文は直せない。
    labels = moved.locator(".timeline li").all_inner_texts()
    report.check(
        len(labels) == 2 and "文を作った" in labels[0] and "送った" in labels[1],
        f"履歴が違う: {labels}",
    )
    # もう一度送る（2026-11-07 PO）：同じ文をコピーし、状態は返事待ちのまま、経緯に時刻だけ。
    reshare = moved.locator("[data-reshare]")
    report.check(reshare.count() == 1, "開いた返事待ちの行に［もう一度送る］が無い")
    reshare.click()
    page.get_by_text("もう一度送ったと記録しました").wait_for()
    report.check("火曜の延長" in page.evaluate("navigator.clipboard.readText()"), "同じ文でない")
    again = next(i for i in request("GET", "/api/board", code)["items"] if i["id"] == childcare_id)
    report.check(again["status"] == "頼んだ", "もう一度送ると状態が変わった")
    report.check(again["events"][-1]["label"] == "もう一度共有した", "経緯に記録が無い")
    moved.locator(".timeline li").nth(2).wait_for()
    report.check(
        "もう一度共有した" in moved.locator(".timeline li").nth(2).inner_text(), "経緯に時刻が無い"
    )
    # 開いた返事待ちの行（もう一度送る・元に戻す・鉛筆・ゴミ箱）が 360px の幅を超えない（UX Design の P1）。
    inspect(page, "21c-reshare-open", out, report)
    moved.get_by_role("button", name="直す", exact=True).click()
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
    family_card.get_by_role("button", name="LINEなどで送る").click()
    family_card.get_by_role("button", name="送った", exact=True).wait_for()
    text = page.evaluate("window.__shared.text")
    report.check("着替えを追加" in text, "共有した文に用件が無い")
    link = re.search(r"http\S+/m#\S+", text)
    report.check(link is not None, "共有した文に1件のリンクが無い")
    item_id = family_card.get_attribute("data-item")
    # LINEから戻ると開き直されることがある。［送ったと記録］は残る。
    page.reload(wait_until="networkidle")
    open_sends(page)
    family_card.get_by_role("button", name="送った", exact=True).click()
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
    first_answer(h_page).wait_for()
    report.check(code not in h_page.evaluate("location.pathname"), "家族コードがパスに入っている")
    # 押す前は4つとも同じ見た目（1つだけ塗ると、選択済みに見える）。
    looks = h_page.locator("button.answer").evaluate_all(
        "els => els.map((el) => { const s = getComputedStyle(el); "
        "return [s.backgroundColor, s.color, s.borderColor, el.getBoundingClientRect().height]; })"
    )
    report.check(len(looks) == 3, f"家族の選択が{len(looks)}個")
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
    first_answer(h_page).wait_for()
    # 全部終わると、一覧は出ない。
    for entry in expected:
        request(
            "PATCH",
            f"/api/board/items/{entry['id']}",
            code,
            {"version": request("GET", "/api/board", code)["version"], "status": "完了"},
        )
    h_page.reload(wait_until="networkidle")
    first_answer(h_page).wait_for()
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
    reply_one(h_page, "持っていく")
    h_page.get_by_text(re.compile("「(持っていく|引き受ける)」と送りました")).wait_for()
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
    reply_one(h_page, "時間を相談したい")
    h_page.get_by_text("「時間を相談したい」と送りました").wait_for()
    home_card.get_by_text("時間を相談したい").wait_for(timeout=5000)

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
    start_home(page, "家庭Bで試す")
    to_now(page)
    code = page.evaluate("location.hash.slice(1)")
    dismiss_save_link(page)
    # 作り手ルール②（PO の決定 A）：結果の前は「候補日ごとに空きを確認する」。押すと条件の段の案。
    page.locator("[data-next-step]").get_by_text("候補日ごとに空きを確認する").wait_for()
    page.locator("[data-now-actions]").get_by_role("button", name="確認の文を作る").click()
    plan_button(page).wait_for(timeout=15000)
    plan_button(page).click()
    quit_plan(page)
    page.locator("[data-next-step]").wait_for()
    report.check("候補日ごとに空きを確認する" not in now_text(page), "押した後も確認が出ている")
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
        return now_text(page)

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
    report.check(page.locator("[data-now-actions] [data-primary]").count() == 0, "主なボタンがある")

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
    report.check("受け取ったら済にする" in text, f"引き受けたとき: {text}")
    report.check("登録の確認" in text, f"何を引き受けたかが無い: {text}")
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
        page.locator("[data-now-actions] [data-primary]").count() == 0,
        "固定文の横に主なボタンがある",
    )
    color = page.locator("[data-now-medical]").evaluate("el => getComputedStyle(el).color")
    report.check(not is_reddish(color), f"固定文に赤を使っている: {color}")
    report.check(
        page.locator("body").inner_text().count(MEDICAL_FIXED_TEXT) == 1, "固定文が2回出ている"
    )
    inspect(page, "b-now-medical", out, report)
    # ［わかった］で閉じると、次の用事へ戻る。足元の固定文も戻る。開き直しても閉じたまま。
    page.locator("[data-now-actions]").get_by_role("button", name="わかった").click()
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
    start_home(page, "家庭Aで試す")
    to_now(page)
    code = page.evaluate("location.hash.slice(1)")
    dismiss_save_link(page)
    page.evaluate("localStorage.setItem('ensemble.consented', 'true')")
    page.reload(wait_until="networkidle")
    page.locator("[data-next-step]").wait_for()

    # 入口は「いま」。開き直しても読み直しても、今の画面のまま。
    report.check(
        page.locator(".bottom-nav [aria-current='page']").inner_text().strip() == "いま",
        "入口が「いま」でない",
    )
    labels = [text.strip() for text in page.locator(".bottom-nav button").all_inner_texts()]
    report.check(
        labels == ["いま", "やること", "カレンダー", "さがす", "設定"],
        f"ナビの語が違う: {labels}",
    )
    # 初期データの返事を「届きました」と言わない。
    report.check(
        page.locator("[data-now-reply]").count() == 0, "開いた直後に返事のカードが出ている"
    )
    report.check(
        "受け取ったら完了" not in now_text(page),
        "初期データの返事を完了へ促している",
    )
    # 何をするかが最初に見える。主なボタンは1画面の中。
    tops = page.evaluate("""() => ['[data-next-step]', '[data-schedule]'].map(
      (s) => document.querySelector(s).getBoundingClientRect().top)""")
    # 図の③：病院で聞いた予定が先、次にできることがその下。
    report.check(tops[1] < tops[0], "病院で聞いた予定が次にできることより下にある")
    body = page.locator("body").inner_text()
    report.check("ボード" not in body, "「ボード」が画面に残っている")

    # 案の前：医師の言葉の節は原文のまま。日付は別の列。「候補」の札は出さない。
    schedule = page.locator("[data-schedule]")
    clauses = schedule.locator("[data-clause]")
    # 図の③：一番上に検査の日（「明日の採血しだい」の日と採血）、その下に条件の節を原文のまま。
    headline = schedule.locator("[data-schedule-headline]")
    report.check(
        headline.count() == 1 and "採血" in headline.inner_text(), "検査の日が一番上にない"
    )
    report.check(clauses.count() == 3, f"節の数が違う: {clauses.count()}")
    report.check(
        "「よければ木曜に帰れるかも」" in clauses.first.inner_text(), "節が原文のままでない"
    )
    report.check(clauses.first.locator(".clause-days").count() == 1, "日付が別の列にない")
    report.check("候補" not in schedule.inner_text(), "案の前に「候補」の札が出ている")
    report.check("未定" not in schedule.inner_text(), "言っていないことが出ている")
    report.check(page.locator("[data-after-entry]").count() == 1, "結果を入れる入口が無い")
    inspect(page, "now-before-plan", out, report)

    # メニュー。ナビにない入口はここに集める。これまでに入れたものの中から、相手ごとの一覧へ。
    from_menu(page, "これまでに入れたもの")
    page.locator("[data-menu-section='entered']").wait_for()
    page.get_by_role("button", name="相手ごと", exact=True).click()
    page.locator("[data-part='職場']").wait_for()
    nav(page, "設定")
    page.locator("[data-settings]").wait_for()
    inspect(page, "settings", out, report)
    settings_text = page.locator("[data-settings]").inner_text()
    for word in ("あなたと家族のこと", "家族も見られるリンク", "これまでに入れたもの", "自分のこと",
                 "頼れる人と支援", "データの扱いと、記録を消す"):  # fmt: skip
        report.check(word in settings_text, f"設定に「{word}」が無い")
    report.check(page.locator(".bottom-nav").count() == 1, "設定に下のナビが無い")
    page.get_by_role("button", name="メニュー", exact=True).click()
    page.locator("[data-menu]").wait_for()
    inspect(page, "menu", out, report)
    menu_text = page.locator("[data-menu]").inner_text()
    for word in ("このアプリについて", "使い方", "同意と線引き", "家庭A・Bで試す"):
        report.check(word in menu_text, f"メニューに「{word}」が無い")
    report.check("あなたと家族のこと" not in menu_text, "メニューに設定の行が残っている")
    report.check(page.locator("[data-menu-section]").count() == 1, "メニューの段が1つでない")
    report.check(page.locator(".bottom-nav").count() == 0, "メニューにナビが出ている")
    close_menu(page)
    nav(page, "設定")
    page.get_by_role("button", name="これまでに入れたもの", exact=True).click()
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
    report.check(
        added["text_original"] == "月曜にもう一回とると言われた",
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
        page.locator("[data-now-actions] [data-primary]").count() == 1, "主なボタンが1つでない"
    )

    # 案を作れないとき：通信の失敗は［もう一度］、⑤の直後は［元に戻す］も。上限は押し直しを出さない。
    states_in_failure(page, code, report)
    # 案を進めると、候補日と札が出る。取り消した日は残す。
    # 読み進めた位置から押しても、案の画面は頭から開く（デモ動画作成の指摘）。
    plan_button(page)
    page.evaluate("scrollTo(0, document.documentElement.scrollHeight)")
    page.wait_for_timeout(100)
    page.get_by_role("button", name="変わるところを見る").click()
    page.get_by_role("button", name=re.compile("送る文を見る・")).wait_for()
    page.wait_for_timeout(100)
    report.check(page.evaluate("scrollY") == 0, "案の画面が途中から開く")
    accept_plan(page)
    back_from_map(page)
    to_now(page)
    # 図の⑨：結果を入れた後は、医師の言葉の欄でなく「次の予定」の1枚。節も根拠も出さない。
    report.check(page.locator("[data-clause]").count() == 0, "案の後に節が残っている")
    upcoming = page.locator("[data-upcoming]")
    day_text = upcoming.inner_text()
    report.check("次は" in day_text and "検査" in day_text, f"次の検査の日が無い: {day_text}")
    report.check(
        "退院の候補" in day_text and "見込み" in day_text, f"残っている候補日が無い: {day_text}"
    )
    report.check("退院日は未定" not in day_text, "候補が残っているのに「退院日は未定」")
    inspect(page, "now-after-plan", out, report)
    send = page.locator("[data-now-actions]").get_by_role(
        "button", name=re.compile("人分を見る|送る文を見る")
    )
    report.check(send.count() == 1, "下書きの主なボタンが無い")
    context.close()


def states_in_failure(page: Page, code: str, report: Report) -> None:
    """案を待っている案を閉じて、失敗の見せ方を確かめ、もとの案へ戻す。"""
    section = page.locator("[data-next-step]")
    actions = page.locator("[data-now-actions]")
    answer = {"status": 503, "detail": "案を出せませんでした。入れた文は残っています"}

    def fail(route) -> None:
        route.fulfill(
            status=answer["status"],
            content_type="application/json",
            body=json.dumps({"detail": answer["detail"]}, ensure_ascii=False),
        )

    # いまの案をやめて、結果をもう一度入れる。
    plan_button(page).click()
    quit_plan(page)
    section.wait_for()
    items = request("GET", "/api/board", code)
    result = items["items"][-1]
    request("DELETE", f"/api/board/items/{result['id']}?version={items['version']}", code)
    page.reload(wait_until="networkidle")
    page.route("**/api/board/proposal", fail)
    page.locator("[data-after-entry]").click()
    page.locator("[data-option='1']").click()
    page.locator(".dock [data-primary]").click()
    actions.get_by_role("button", name="もう一度").wait_for()
    text = now_text(page)
    report.check("案を出せませんでした" in text, f"失敗の文が無い: {text}")
    report.check("選んだ内容は残っています" in text, f"⑤の直後の文が無い: {text}")
    report.check(actions.get_by_role("button", name="元に戻す").count() == 1, "⑤の直後に戻せない")
    inspect(page, "now-503-after", OUT_FOR_STATES, report)
    # 回数の上限。押し直しを出さない。
    answer.update(status=429, detail="今日の回数の上限に届きました。入れた文は残っています")
    actions.get_by_role("button", name="もう一度").click()
    section.get_by_text("今日の回数の上限に届きました").wait_for()
    report.check(
        actions.get_by_role("button", name="もう一度").count() == 0, "上限で押し直しが出た"
    )
    inspect(page, "now-429", OUT_FOR_STATES, report)
    actions.get_by_role("button", name="閉じる").click()
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
    start_home(page, "家庭Aで試す")
    to_now(page)
    code = page.evaluate("location.hash.slice(1)")
    dismiss_save_link(page)
    tab(page, "進捗別")

    def count(word: str) -> int:
        column = page.locator(f"[data-column='{word}']")
        if column.count() == 0:
            return 0
        return int(column.inner_text().replace(word, "").strip())

    def rows_in(*groups: str) -> int:
        return sum(page.locator(f"[data-lane='{group}'] .row").count() for group in groups)

    # 2026-11-08 タスク管理：一覧（前の1件ずつ）の上の1行は「担当が決まっていない・引き受けた・期限を過ぎた」。
    def summed(word: str) -> int:
        found = re.search(rf"{word} (\d+)", page.locator("[data-task-sum]").inner_text())
        return int(found.group(1)) if found else 0

    def consistent() -> None:
        states = page.locator("[data-task-state]")
        by = lambda name: page.locator(f"[data-task-state='{name}']").count()  # noqa: E731
        report.check(states.count() > 0, "一覧にやることが無い")
        report.check(
            summed("担当が決まっていない") == by("undecided"),
            "「担当が決まっていない」の数と行が合わない",
        )
        report.check(summed("引き受けた") == by("accepted"), "「引き受けた」の数と行が合わない")
        report.check(summed("期限を過ぎた") == by("overdue"), "「期限を過ぎた」の数と行が合わない")

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
    first.locator("[data-card-action='done']").click()
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
    start_home(parent, "家庭Aで試す")
    to_parts(parent)
    code = parent.evaluate("location.hash.slice(1)")
    dismiss_save_link(parent)
    version = request("GET", "/api/board", code)["version"]
    request("PATCH", "/api/board/items/a-02", code, {"version": version, "status": "頼んだ"})
    later = datetime.now() + timedelta(days=2)

    # 日数はサーバーの今日で数える。2日後のサーバーを再現する。
    def two_days_later(route) -> None:
        response = route.fetch()
        route.fulfill(
            response=response, json={**response.json(), "today": later.date().isoformat()}
        )

    # 親のタブ：いつもの語と日数。
    parent.clock.set_fixed_time(later)
    parent.route("**/api/board", two_days_later)
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
    # 2026-11-07 PO：夫も「入院の全体を見る」から、見るだけの「いま」へ入る。
    husband.get_by_role("link", name="入院の全体を見る").click()
    husband.locator(".now-head").wait_for()
    report.check(
        husband.get_by_role("button", name="自分へのお願いに戻る").count() == 1,
        "夫が用事へ戻れない",
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
        # 数の1行（夫の用事だけ）は「あなた」で書く（UX Design の P2 ③）。カードと見出しは親の視点のまま。
        "あなた"
        not in husband.locator("[data-lanes]")
        .inner_text()
        .replace(husband.locator("[data-progress-sum]").inner_text(), ""),
        "夫のタブに「あなた」",
    )
    inspect(husband, "2n-lanes-from-link", out, report)
    # 「いま」は親の視点なので出さない。開き直しても同じタブなら変わらない。親のタブはそのまま。
    labels = [text.strip() for text in husband.locator(".bottom-nav button").all_inner_texts()]
    report.check(
        labels == ["いま", "やること", "カレンダー", "さがす"], f"夫のタブのナビ: {labels}"
    )
    report.check(
        "親の未送信" not in husband.locator("[data-progress-sum]").inner_text()
        and "あなた" in husband.locator("[data-progress-sum]").inner_text(),
        "夫のタブの上の1行の語が違う",
    )
    husband.reload(wait_until="networkidle")
    report.check(husband.locator("[data-next-step]").count() == 0, "夫のタブに「いま」が出た")
    # 夫の「いま」は見るだけ（2026-11-07 PO・UX Design）。病院で聞いた予定は見え、親が押すものは無い。
    nav(husband, "いま")
    husband.locator("[data-schedule], [data-upcoming]").first.wait_for()
    for selector, what in (("[data-next-step]", "次にできること"), ("[data-after-entry]", "結果を入れる入口"),
                           ("[data-now-input]", "入れる欄"), ("[data-now-actions]", "下の帯のボタン"),
                           ("[data-add-fab]", "右下の丸"), ("[data-save-link-line]", "URLの案内")):  # fmt: skip
        report.check(husband.locator(selector).count() == 0, f"夫の「いま」に{what}が出ている")
    inspect(husband, "husband-now", out, report)
    husband.get_by_role("button", name="メニュー", exact=True).click()
    menu_text = husband.locator("[data-menu]").inner_text()
    report.check("あなたと家族のこと" not in menu_text, "夫のメニューに家族設定が出ている")
    report.check("この記録" not in menu_text, "夫のメニューに消す入口が出ている")
    close_menu(husband)
    husband.get_by_role("button", name="自分へのお願いに戻る").click()
    husband.get_by_role("heading", name="夫へ、病室からのお願い").wait_for()
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
    start_home(page, "家庭Aで試す")
    to_parts(page)
    code = page.evaluate("location.hash.slice(1)")
    dismiss_save_link(page)
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
    top = now_text(page)
    report.check("ほか" not in top and "送る文を見る" in top, f"「いま」の下書きの数が違う: {top}")
    open_map(page)
    page.get_by_text("まだ送っていません", exact=False).first.wait_for()
    page.wait_for_timeout(300)
    text = page.locator("body").inner_text()
    report.check("送る文が1件あります" in text, "地図が前の案の家族の文まで数えている")
    context.close()


def tab_position(browser, out: Path, report: Report) -> None:
    """下のナビを押すと、開いた画面の頭から見える。どの大きさでも（QA TestのP1-B）。"""
    pairs = ("いま", "やること", "カレンダー", "さがす", "設定")
    for width, height in ((360, 740), (360, 800), (390, 844), (412, 915)):
        context = browser.new_context(
            viewport={"width": width, "height": height},
            device_scale_factor=2,
            color_scheme="light",
            has_touch=True,
        )
        page = context.new_page()
        page.goto(BASE + "/", wait_until="networkidle")
        start_home(page, "家庭Aで試す")
        to_parts(page)
        dismiss_save_link(page)
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
            page.locator(".bottom-nav [aria-current='page']").inner_text().strip() == "やること",
            "週の行を押してもメッセージへ行かない",
        )
        context.close()


def plan_and_drafts(browser, out: Path, report: Report) -> None:
    """⑥案の画面に線とカードが出て、押す前は台帳が変わらない。⑦は相手ごとに送って次へ移る。"""
    context = browser.new_context(
        viewport={"width": WIDTH, "height": HEIGHT},
        device_scale_factor=2,
        color_scheme="light",
        has_touch=True,
    )
    context.grant_permissions(["clipboard-read", "clipboard-write"], origin=BASE)
    page = context.new_page()
    page.goto(BASE + "/", wait_until="networkidle")
    start_home(page, "家庭Aで試す")
    to_now(page)
    code = page.evaluate("location.hash.slice(1)")
    dismiss_save_link(page)
    page.evaluate("localStorage.setItem('ensemble.consented', 'true')")
    page.reload(wait_until="networkidle")
    page.locator("[data-after-entry]").click()
    page.locator("[data-option='1']").click()
    page.locator(".dock [data-primary]").click()
    plan_button(page).wait_for(timeout=15000)
    plan_button(page).click()

    # ⑥：最初は「変わるところだけ」。線とカードは「案の中身を見る」の先。件数はコードで数える。
    page.locator("[data-decide-summary]").wait_for()
    count_text = page.locator("[data-plan-count]").inner_text()
    report.check("人分の文にまとめます" in count_text, f"件数の1行が違う: {count_text}")
    plan_detail(page)
    nodes = page.locator(".map-node")
    nodes.first.wait_for()
    report.check(page.locator(".map-line").count() > 0, "案の画面に線が無い")
    report.check(page.locator(".week-strip").count() == 1, "案の画面に帯が無い")
    report.check(
        "月曜にもう一回とると言われた" in page.locator("main").inner_text(),
        "入れた内容が出ていない",
    )
    report.check("「『" not in page.locator("main").inner_text(), "かぎかっこが二重になっている")
    review = page.locator("[data-review]")
    report.check("退院の候補日（医師の言葉から）" in review.inner_text(), "⑥の見出しが違う")
    report.check(
        review.inner_text().count("（火）") == 1, f"火曜が2回出ている: {review.inner_text()}"
    )
    for word in ("検査", "見込み", "取り消し"):
        report.check(word in review.inner_text(), f"⑥に「{word}」の札が無い")
    page.wait_for_timeout(5000)
    inspect(page, "six-plan", out, report)
    fixed_note(page, "変わるところ", report)
    before = request("GET", "/api/board", code)
    report.check(before["branches"] == [], "押す前に案が台帳に入った")
    # カードを押すと、変更前と後、根拠が読める。押しても台帳は変わらない。
    page.locator(".map-node[data-part='家族']").click()
    page.get_by_role("heading", name="夫に頼む").wait_for()
    report.check("根拠" in page.locator("main").inner_text(), "カードに根拠が無い")
    inspect(page, "six-card", out, report)
    page.get_by_role("button", name="戻る", exact=True).click()
    nodes.first.wait_for()
    report.check(request("GET", "/api/board", code) == before, "カードを開いて台帳が変わった")
    # 文を直すときは、相手ごとの一覧に切り替わる。
    edit_plan(page)
    page.locator("[data-action='職場'] .plan-toggle").wait_for()
    report.check(nodes.count() == 0, "一覧と線が同時に出ている")
    press_accept(page)

    # ⑦：進めたら相手ごとの下書きへ。共有かコピーの後に［送った］が出る。
    tabs = page.locator("[data-draft-tabs]")
    tabs.wait_for()
    labels = tabs.get_by_role("tab").all_inner_texts()
    report.check(len(labels) == 3 and labels[0] == "夫", f"タブが違う: {labels}")
    report.check("3人分の送る文" in tabs.inner_text(), "人数の1行が無い")
    told = tabs.get_by_role("button", name="送った", exact=True)
    report.check(told.count() == 0, "送る前に［送った］が出ている")
    for name in ("コピー", "文を直す", "あとで"):
        report.check(tabs.get_by_role("button", name=name).count() == 1, f"［{name}］が無い")
    report.check(page.evaluate("scrollY") == 0, "下書きの画面が途中から開く")
    inspect(page, "seven-tabs", out, report)
    # 文を直すと「あなたが編集済み」（図の⑦）。
    tabs.get_by_role("button", name="文を直す").click()
    box = tabs.get_by_role("textbox")
    box.fill(box.input_value() + "土曜でもいいよ。")
    tabs.get_by_role("button", name="この文にする").click()
    tabs.get_by_text("あなたが編集済み").wait_for()
    report.check("土曜でもいいよ" in tabs.inner_text(), "直した文が出ない")
    inspect(page, "seven-edited", out, report)
    # 別のタブへ移れる。
    tabs.get_by_role("tab", name="職場").click()
    report.check(tabs.locator("[data-draft='職場']").count() == 1, "タブを押しても移らない")
    tabs.get_by_role("tab", name="夫").click()
    # コピーの後に［送った］。押したら次の相手へ自動で移る。戻せる。
    tabs.get_by_role("button", name="コピー").click()
    told.wait_for()
    copied = page.evaluate("navigator.clipboard.readText()")
    report.check("土曜でもいいよ" in copied, "直した文がコピーされない")
    report.check("/m#" in copied, "夫の文に返事のリンクが無い")
    told.click()
    page.get_by_text("送りました").wait_for()
    tabs.locator("[data-draft='夫'], [data-draft='家族']").wait_for(state="detached")
    report.check(tabs.get_by_role("tab").count() == 2, "送った相手のタブが残っている")
    # あとでを押すと閉じる。いまの主なボタンから戻れる。
    tabs.get_by_role("button", name="あとで").click()
    tabs.wait_for(state="detached")
    to_now(page)
    page.locator("[data-now-actions]").get_by_role("button", name="送る文を見る").click()
    tabs.wait_for()
    # 全部送るとタブは消える。
    for _ in range(2):
        tabs.get_by_role("button", name="コピー").click()
        told.wait_for()
        told.click()
        page.wait_for_timeout(400)
    tabs.wait_for(state="detached")
    to_now(page)
    report.check(page.locator("[data-map-open]").count() == 1, "全部送った後に地図へ戻れない")
    context.close()


WORK_FIRST_TEXT = (
    "会社に休みの連絡をする。今日から入院。いつまでかは分からない。木曜の定例会議は出られない"
)
WORK_FIRST_ADDED = "よろしくお願いします。"


def work_first(browser, out: Path, report: Report) -> None:
    """入院初日の職場への文（v3.5.11）。親の職場あての頼みごとに［送る文を見る］→ 作る →
    送る文の画面で確かめて直す → コピー → ［送った］。台帳は［送った］まで変わらない。"""
    context = browser.new_context(
        viewport={"width": WIDTH, "height": HEIGHT},
        device_scale_factor=2,
        color_scheme="light",
        has_touch=True,
    )
    context.grant_permissions(["clipboard-read", "clipboard-write"], origin=BASE)
    page = context.new_page()
    page.goto(BASE + "/", wait_until="networkidle")
    start_home(page, "家庭Aで試す")
    to_now(page)
    code = page.evaluate("location.hash.slice(1)")
    dismiss_save_link(page)
    page.evaluate("localStorage.setItem('ensemble.consented', 'true')")
    board = request("GET", "/api/board", code)
    report.check(board.get("work_first") is True, "職場への文の切り替えが on になっていない")
    added = request(
        "POST",
        "/api/board/items",
        code,
        {
            "version": board["version"],
            "kind": "行動",
            "part": "職場",
            "certainty": "見込み",
            "text_original": WORK_FIRST_TEXT,
            "task_summary": "休みの連絡",
            "speaker": "親",
            "status": "頼みたい",
        },
    )
    item_id = added["items"][-1]["id"]
    page.reload(wait_until="networkidle")
    to_parts(page)
    open_part(page, "職場")
    row = page.locator(f"[data-item='{item_id}']")
    row.wait_for()
    make = row.locator("[data-work-first]").get_by_role("button", name="送る文を見る")
    report.check(make.count() == 1, "職場の頼みごとに［送る文を見る］が無い")
    # 上司の文（原文を保存していない第三者の文）は、親の頼みごとではないので出さない。
    report.check(
        page.locator("[data-item='a-06'] [data-work-first]").count() == 0,
        "上司の文に［送る文を見る］が出ている",
    )
    row.scroll_into_view_if_needed()
    inspect(page, "work-first-row", out, report, reach=False)
    before = request("GET", "/api/board", code)

    make.click()
    tabs = page.locator("[data-draft-tabs]")
    card = tabs.locator("[data-draft='職場']")
    card.wait_for(timeout=15000)
    page.wait_for_timeout(400)
    report.check(
        tabs.get_by_role("tab", name="職場").get_attribute("aria-selected") == "true",
        "作った後に職場のタブが開いていない",
    )
    text = card.inner_text()
    for words in ("子どもの入院に付き添う", "木曜の定例会議", "代役か延期か", "扱いはご相談"):
        report.check(words in text, f"職場への文に「{words}」が無い: {text[:80]}")
    report.check(request("GET", "/api/board", code) == before, "作っただけで台帳が変わった")
    inspect(page, "work-first-draft", out, report, reach=False)

    # 文を直す。親が直した文には医療の語だけ当てる。台帳の親の原文は変えない。
    tabs.get_by_role("button", name="文を直す").click()
    box = tabs.get_by_role("textbox")
    draft = box.input_value()
    report.check(draft.startswith("お疲れさまです。"), f"直す欄に作った文が入っていない: {draft}")
    box.fill("お薬の量が変わりました。")
    tabs.get_by_role("button", name="この文にする").click()
    page.get_by_text("医療の語が入っている文は送れません").first.wait_for()
    report.check(box.count() == 1, "医療の語で断った後に直す欄が閉じた")
    box.fill(draft + WORK_FIRST_ADDED)
    tabs.get_by_role("button", name="この文にする").click()
    tabs.get_by_text("あなたが編集済み").wait_for()
    report.check(WORK_FIRST_ADDED in card.inner_text(), "直した文が出ない")
    after_edit = request("GET", "/api/board", code)
    target = next(item for item in after_edit["items"] if item["id"] == item_id)
    report.check(target["text_original"] == WORK_FIRST_TEXT, "直した文で台帳の原文が変わった")
    report.check(after_edit["version"] == before["version"], "直しただけで台帳の版が進んだ")

    # コピーの後に［送った］。押したときだけ状態が変わる。
    tabs.get_by_role("button", name="コピー").click()
    told = tabs.get_by_role("button", name="送った", exact=True)
    told.wait_for()
    copied = page.evaluate("navigator.clipboard.readText()")
    report.check(WORK_FIRST_ADDED in copied, "直した文がコピーされない")
    report.check("/m#" not in copied, "職場への文に返事のリンクが付いている")
    told.click()
    page.get_by_text("送りました").wait_for()
    sent = request("GET", "/api/board", code)
    target = next(item for item in sent["items"] if item["id"] == item_id)
    report.check(target["status"] == "頼んだ", f"［送った］で状態が変わらない: {target['status']}")
    report.check(target["text_original"] == WORK_FIRST_TEXT, "送った後に台帳の原文が変わった")
    report.check(card.count() == 0, "送った後も職場の下書きが残っている")
    context.close()


def own_home(browser, out: Path, report: Report) -> None:
    """①自分の家庭で始める。選ぶ形だけ。未設定と「いない」を分ける。一往復が閉じる。"""
    for label, errand, supports in (
        ("頼める人がいる", "夫・パートナー", []),
        ("頼める人がいない", "いない", ["延長保育", "ファミサポ"]),
    ):
        context = browser.new_context(
            viewport={"width": WIDTH, "height": HEIGHT},
            device_scale_factor=2,
            color_scheme="light",
            has_touch=True,
        )
        page = context.new_page()
        # 図の①：開くとすぐ「設定 1/2」。
        page.goto(BASE + "/", wait_until="networkidle")
        screen = page.locator("[data-setup-screen]")
        screen.wait_for()
        report.check(
            "実在の人の名前や病名は入れないでください" in screen.inner_text(), "注意の文が無い"
        )
        # 自由な文字の欄は、子のあだ名（8字まで）の1つだけ。入院した日は日付の欄。
        free_text = page.locator("input:not([type=date]), textarea")
        report.check(free_text.count() == 1, "あだ名のほかに自由な文字の欄がある")
        inspect(page, f"setup-1-{errand}", out, report)
        # 断るあだ名（1文字。2026-11-08 本人の実機で「み」）：押せない理由が「次へ」の近くにも見える。
        nickname = page.locator("[data-nickname] input")
        nickname.fill("み")
        report.check(
            page.get_by_role("button", name="次へ").is_disabled(), "断るあだ名で次へが押せる"
        )
        near = page.locator("[data-nickname-refused-near]")
        box = near.bounding_box() if near.count() else None
        report.check(
            box is not None and box["y"] >= 0 and box["y"] + box["height"] <= HEIGHT,
            "押せない理由がボタンの近くの見える位置に無い",
        )
        if errand == "夫・パートナー":
            inspect(page, "setup-1-nickname-refused", out, report)
        nickname.fill("")
        report.check(near.count() == 0, "あだ名を消しても理由が残っている")
        page.locator("[data-setup='parent:母']").click()
        page.locator("[data-setup='school:保育園']").click()
        page.locator("[data-setup='work:会社員']").click()
        page.get_by_role("button", name="次へ").click()
        page.locator(f"[data-setup='errand:{errand}']").click()
        if errand != "いない":
            page.locator("[data-setup='errand_time:平日の夜と朝']").click()
        for name in supports:
            page.locator(f"[data-setup='supports:{name}']").click()
        report.check(page.get_by_role("textbox").count() == 0, "2ページ目に自由な文字の欄がある")
        inspect(page, f"setup-2-{errand}", out, report)
        page.get_by_role("button", name="設定して始める").click()
        page.locator("[data-next-step]").wait_for()
        code = page.evaluate("location.hash.slice(1)")
        dismiss_save_link(page)
        board = request("GET", "/api/board", code)
        report.check(board["profile"]["configured"] is True, "設定済みにならない")
        if errand == "いない":
            report.check(board["profile"]["errand_people"] == [], f"{label}: 頼める人が入った")
            report.check(len(board["profile"]["supports"]) == 2, f"{label}: 支援が入らない")
        else:
            report.check(
                board["profile"]["errand_people"][0]["label"] == errand, f"{label}: 頼める人が違う"
            )
        report.check(
            "まだ何も入っていません" in now_text(page), f"{label}: 最初のいまが空の形でない"
        )
        inspect(page, f"own-now-{errand}", out, report)
        # 医師の言葉を入れる→案→進める→下書き→返事の記録まで一往復。
        page.evaluate("localStorage.setItem('ensemble.consented', 'true')")
        page.reload(wait_until="networkidle")
        add_text(
            page,
            "先生：明日の採血しだい。よければ木曜に帰れるかも。だめなら月曜にもう一回とって、それで大丈夫なら火曜かな",
        )
        tabs = page.locator("[data-draft-tabs]")
        # 医師の言葉の直後は案を自動で作らない（図の③）。説明を聞いたあとの選択で結果を入れ、
        # 案を進める。家族への案は頼める人がいるときだけ。
        to_now(page)
        page.locator("[data-after-entry]").wait_for(timeout=15000)
        page.locator("[data-after-entry]").click()
        page.locator("[data-option='1']").click()
        page.locator(".dock [data-primary]").click()
        plan_button(page).wait_for(timeout=15000)
        plan_button(page).click()
        press_accept(page)
        tabs.wait_for()
        labels = tabs.get_by_role("tab").all_inner_texts()
        report.check(
            (errand in labels) == (errand != "いない"),
            f"{label}: 家族の下書きのタブが違う: {labels}",
        )
        if errand == "いない":
            report.check("支援" in labels, f"{label}: 支援の送る文が無い: {labels}")
        tabs.get_by_role("button", name="コピー").first.click()
        tabs.get_by_role("button", name="送った", exact=True).click()
        page.wait_for_timeout(500)
        report.check(tabs.get_by_role("tab").count() == len(labels) - 1, "送っても次へ移らない")
        context.close()

    # あとで設定すると未設定のまま。いまに案内が出て、設定できる。
    context = browser.new_context(
        viewport={"width": WIDTH, "height": HEIGHT},
        device_scale_factor=2,
        color_scheme="light",
        has_touch=True,
    )
    page = context.new_page()
    page.goto(BASE + "/", wait_until="networkidle")
    page.get_by_role("button", name="あとで設定する").click()
    page.locator("[data-next-step]").wait_for()
    code = page.evaluate("location.hash.slice(1)")
    dismiss_save_link(page)
    board = request("GET", "/api/board", code)
    report.check(board["profile"]["configured"] is False, "未設定と「いない」が分かれていない")
    note = page.locator("[data-setup-note]")
    report.check("家族の設定がまだです" in note.inner_text(), "未設定の案内が無い")
    inspect(page, "own-unset", out, report)
    note.get_by_role("button", name="設定する").click()
    page.locator("[data-setup-screen]").wait_for()
    page.get_by_role("button", name="次へ").click()
    page.locator("[data-setup='errand:祖父母']").click()
    page.locator("[data-setup='errand_time:休みの日だけ']").click()
    page.get_by_role("button", name="設定して始める").click()
    page.locator("[data-next-step]").wait_for()
    board = request("GET", "/api/board", code)
    report.check(board["profile"]["configured"] is True, "直しても設定済みにならない")
    report.check(board["profile"]["errand_people"][0]["label"] == "祖父母", "直した内容が入らない")
    report.check(page.locator("[data-setup-note]").count() == 0, "設定した後も案内が出る")
    # メニューから直せる。前に選んだ内容が残っている。
    from_menu(page, "あなたと家族のこと")
    page.locator("[data-setup-screen]").wait_for()
    report.check(
        "設定 1/2" not in page.locator("[data-setup-screen]").inner_text(), "直すのに「設定 1/2」"
    )
    # 直すときに断るあだ名を入れても、［戻る］で保存せずに閉じられる（2026-11-08 夫の初見：閉じ込められた）。
    page.locator("[data-nickname] input").fill("み")
    report.check(page.get_by_role("button", name="次へ").is_disabled(), "断るあだ名で次へが押せる")
    page.get_by_role("button", name="戻る", exact=True).click()
    page.locator("[data-setup-screen]").wait_for(state="detached")
    report.check(
        request("GET", "/api/board", code)["nickname"] != "み", "戻るで断るあだ名が保存された"
    )
    from_menu(page, "あなたと家族のこと")
    page.locator("[data-setup-screen]").wait_for()
    # 札は配偶者の語。値（台帳とAIへの語）は変えない。
    page.get_by_role("button", name="次へ").click()
    report.check(
        page.locator("[data-setup='errand:夫・パートナー']").inner_text().strip()
        == "配偶者・パートナー",
        "頼める人の札が配偶者の語でない",
    )
    report.check(
        page.locator("[data-setup='errand:祖父母']").get_attribute("aria-pressed") == "true",
        "前に選んだ内容が残っていない",
    )
    report.check(
        page.get_by_role("button", name="保存する").count() == 1, "直すのに［保存する］が無い"
    )
    report.check(
        page.get_by_role("button", name="あとで設定する").count() == 0, "直すのに［あとで設定する］"
    )
    context.close()


def reply_states(browser, out: Path, report: Report) -> None:
    """夫の「時間を相談したい」が親に届く。夫の入口に下書きを出さない。③⑥の札と節。"""
    context = browser.new_context(
        viewport={"width": WIDTH, "height": HEIGHT},
        device_scale_factor=2,
        color_scheme="light",
        has_touch=True,
    )
    page = context.new_page()
    page.goto(BASE + "/", wait_until="networkidle")
    start_home(page, "家庭Aで試す")
    to_now(page)
    code = page.evaluate("location.hash.slice(1)")
    skip_candidate_check(page, code)
    dismiss_save_link(page)
    page.evaluate("localStorage.setItem('ensemble.consented', 'true')")
    page.reload(wait_until="networkidle")
    # 質問の用事は、何をするのかを先に書く。
    next_step = page.locator("[data-next-step]")
    report.check(
        "病院に確認する" in next_step.inner_text(), f"質問の用事の文: {next_step.inner_text()}"
    )
    report.check("退院の時刻" in next_step.inner_text(), "質問の中身が無い")
    # ②これまでに入れたものは、親が入れた文で出す。
    open_add(page)
    recent = page.locator("[data-recent]")
    recent.wait_for()
    report.check("採血の結果で退院候補が変わる" not in recent.inner_text(), "言い換えが出ている")
    page.locator("[data-input-later]").click()
    page.locator("[data-next-step]").wait_for()
    # ⑧3択は縦1列で、語が割れない。
    version = request("GET", "/api/board", code)["version"]
    request("PATCH", "/api/board/items/a-02", code, {"version": version, "status": "頼んだ"})
    husband = context.new_page()
    husband.goto(f"{BASE}/m#{code}/a-02", wait_until="networkidle")
    boxes = [first_answer(husband).bounding_box()] + [
        husband.get_by_role("button", name=name, exact=True).bounding_box()
        for name in ("時間を相談したい", "むずかしい")
    ]
    report.check(len({round(box["x"]) for box in boxes}) == 1, f"3択が縦1列でない: {boxes}")
    report.check(max(box["height"] for box in boxes) <= 60, f"3択の語が割れている: {boxes}")
    inspect(husband, "one-three", out, report)
    # 夫の「時間を相談したい」：親のいまにカードとひとこと。
    page.wait_for_timeout(1100)
    reply_one(husband, "時間を相談したい", "19時以降なら話せる")
    husband.get_by_text("「時間を相談したい」と送りました").wait_for()
    page.reload(wait_until="networkidle")
    card = page.locator("[data-now-reply]")
    card.wait_for()
    report.check("夫：時間を相談したい" in card.inner_text(), f"札が違う: {card.inner_text()}")
    report.check("19時以降なら話せる" in card.inner_text(), "ひとことがカードに無い")
    report.check(card.locator("article button").count() == 0, "保留に完了のボタンが出ている")
    report.check(
        "夫は時間を相談したいそうです" in next_step.inner_text(), f"文: {next_step.inner_text()}"
    )
    report.check(
        page.locator("[data-now-actions] [data-primary]").inner_text() == "用事を見る",
        "主なボタンが違う",
    )
    inspect(page, "now-talk", out, report)
    # 段は「返事待ち」でなく返事の側。進捗と数を揃える。
    to_progress(page)
    report.check(
        page.locator("[data-lane='held'] [data-item='a-02']").count() == 1, "保留の段に無い"
    )
    report.check(
        page.locator("[data-lane='waiting'] [data-item='a-02']").count() == 0,
        "返事待ちに残っている",
    )
    report.check(
        "時間を相談したい" in page.locator("[data-lane='held']").inner_text(), "段の札が違う"
    )
    report.check("保留 1" in page.locator(".lane-sum").inner_text(), "上の数に保留が無い")
    inspect(page, "lanes-held", out, report)
    # 症状のひとこと：固定文はカードだけで、足元に重ねない。
    husband.get_by_role("button", name="答えを変える").click()
    page.wait_for_timeout(1100)
    reply_one(husband, "持っていく", "熱があるみたい")
    husband.get_by_text(re.compile("「(持っていく|引き受ける)」と送りました")).wait_for()
    husband.get_by_role("button", name="答えを変える").click()
    page.wait_for_timeout(1100)
    reply_one(husband, "時間を相談したい", "熱があるみたい")
    husband.get_by_text("「時間を相談したい」と送りました").wait_for()
    to_now(page)
    page.reload(wait_until="networkidle")
    card.wait_for()
    report.check(MEDICAL_FIXED_TEXT in card.inner_text(), "症状のひとことに固定文が無い")
    report.check(page.locator(".fixed-note").count() == 0, "足元にも固定文が出ている")
    # よければの道：⑥の見出しと行、③の分かれ道。
    page.locator("[data-after-entry]").click()
    page.locator("[data-option='0']").click()
    page.locator(".dock [data-primary]").click()
    plan_button(page).wait_for(timeout=15000)
    # 案ができている間に医療の問いを入れても、固定文が先に出る（デモ動画作成の報告）。
    open_add(page)
    page.get_by_role("textbox").fill("この薬、減らしてもいい？")
    page.get_by_role("button", name="整理する", exact=True).click()
    to_now(page)
    page.locator("[data-now-medical]").wait_for(timeout=15000)
    page.get_by_role("button", name="わかった", exact=True).click()
    plan_button(page).click()
    page.locator("[data-decide-summary]").wait_for()
    lines = page.locator("[data-review-line]").all_inner_texts()
    report.check(
        any("退院の候補" in line and "見込み" in line for line in lines),
        f"⑥の見直しに見込みの候補日が無い: {lines}",
    )
    report.check(not any("未定" in line for line in lines), f"候補が残るのに未定: {lines}")
    plan_detail(page)
    review = page.locator("[data-review]")
    review.wait_for()
    text = review.inner_text()
    report.check("退院の候補日（医師の言葉から）" in text, f"⑥の見出し: {text}")
    report.check("よければ木曜に帰れるかも" in text and "見込み" in text, f"⑥の行: {text}")
    report.check(text.count("退院の候補日") == 1, f"⑥の語: {text}")
    inspect(page, "six-good", out, report)
    press_accept(page)
    page.locator("[data-draft-tabs]").wait_for()
    # ⑦の札は1行で、題は頼みごとの文。
    report.check(page.locator(".draft-head .chip").bounding_box()["height"] < 32, "札が割れている")
    report.check(
        page.locator(".draft-head h3").inner_text() == "木曜の夜、家にいられそう？",
        f"⑦の題: {page.locator('.draft-head h3').inner_text()}",
    )
    inspect(page, "seven-ask", out, report)
    # ⑧の上にも頼みごとの文。
    board = request("GET", "/api/board", code)
    sent = next(i for i in board["items"] if i["id"].startswith("s-") and i["part"] == "家族")
    asked = context.new_page()
    asked.goto(f"{BASE}/m#{code}/{sent['id']}", wait_until="networkidle")
    report.check(
        asked.locator(".one-ask").inner_text() == "木曜の夜、家にいられそう？",
        "⑧に頼みごとの文が無い",
    )
    inspect(asked, "one-ask", out, report)
    # 夫の入口の「やりとり」に、親の下書きを出さない。
    husband.reload(wait_until="networkidle")
    # 2026-11-07 PO：夫も「入院の全体を見る」から、見るだけの「いま」へ入る。
    husband.get_by_role("link", name="入院の全体を見る").click()
    husband.locator(".now-head").wait_for()
    to_parts(husband)
    report.check(husband.locator("[data-draft-tabs]").count() == 0, "夫の入口に送る文が出ている")
    # 進み具合の下書きの行にも、送る操作を出さない。
    to_progress(husband)
    drafts = husband.locator("[data-item^='s-']")
    report.check(drafts.count() > 0, "夫の進み具合に下書きの行が無い")
    report.check(husband.locator("[data-send]").count() == 0, "夫の入口に送る印が出ている")
    report.check(drafts.locator(".row-lead.check").count() == 0, "夫が親の下書きを済にできる")
    drafts.first.locator(".row-main").click()
    report.check(
        husband.get_by_role("button", name="コピー").count() == 0
        and husband.get_by_role("button", name="送った", exact=True).count() == 0,
        "夫の入口に下書きの送る操作が出ている",
    )
    opened = drafts.first
    report.check(
        opened.locator(".row-tools button").count() == 0, "夫の入口で親の下書きを直せる・消せる"
    )
    report.check("左の丸" not in opened.inner_text(), "丸が無いのに丸の案内が出ている")
    # 結果を入れた後のいま（図の⑨）：次の予定の1枚に、見込みの木曜。
    to_now(page)
    upcoming = page.locator("[data-upcoming]")
    upcoming.wait_for()
    report.check(
        "（木）" in upcoming.inner_text() and "見込み" in upcoming.inner_text(),
        f"いまに見込みの木曜が無い: {upcoming.inner_text()}",
    )
    # ⑫家族も見られるリンク：［あとで］を出さない。
    from_menu(page, "家族も見られるリンク")
    link = page.locator("[data-save-link]")
    link.wait_for()
    report.check(link.get_by_role("button", name="あとで").count() == 0, "メニューの中に［あとで］")
    context.close()

    # 次の段が無いとき：医師の言葉の節と候補日の両方が出る。⑥も同じ見出し。
    context = browser.new_context(
        viewport={"width": WIDTH, "height": HEIGHT},
        device_scale_factor=2,
        color_scheme="light",
        has_touch=True,
    )
    page = context.new_page()
    page.goto(BASE + "/", wait_until="networkidle")
    start_home(page, "家庭Aで試す")
    to_now(page)
    dismiss_save_link(page)
    page.evaluate("localStorage.setItem('ensemble.consented', 'true')")
    page.reload(wait_until="networkidle")
    add_text(page, "月曜にもう一回とると言われた 次の段なし")
    plan_button(page).wait_for(timeout=15000)
    plan_button(page).click()
    page.locator("[data-decide-summary]").wait_for()
    plan_detail(page)
    review = page.locator("[data-review]")
    review.wait_for()
    report.check(
        "退院の候補日（医師の言葉から）" in review.inner_text(), "次の段が無いときの見出し"
    )
    report.check(
        page.locator("[data-review] [data-candidate-day]").count() == 2, "候補日の行が無い"
    )
    press_accept(page)
    page.locator("[data-draft-tabs]").wait_for()
    to_now(page)
    # 結果の後は次の予定の1枚（図の⑨）。次の段が無くても、残っている候補日は見込みで出る。
    upcoming = page.locator("[data-upcoming]")
    upcoming.wait_for()
    report.check(page.locator("[data-paths]").count() == 0, "次の段が無いのに分かれ道が出ている")
    report.check(
        "退院の候補" in upcoming.inner_text() and "見込み" in upcoming.inner_text(),
        f"候補日が消える: {upcoming.inner_text()}",
    )
    inspect(page, "now-no-next", out, report)
    context.close()

    # ①の未選択は「いない」と読まない。
    context = browser.new_context(
        viewport={"width": WIDTH, "height": HEIGHT},
        device_scale_factor=2,
        color_scheme="light",
        has_touch=True,
    )
    page = context.new_page()
    page.goto(BASE + "/", wait_until="networkidle")
    page.locator("[data-setup='parent:母']").click()
    page.get_by_role("button", name="次へ").click()
    page.get_by_role("button", name="設定して始める").click()
    page.locator("[data-next-step]").wait_for()
    code = page.evaluate("location.hash.slice(1)")
    dismiss_save_link(page)
    board = request("GET", "/api/board", code)
    report.check(board["profile"]["configured"] is False, "未選択が設定済みになった")
    note = page.locator("[data-setup-note]")
    note.wait_for()
    # 設定し直すと、前に選んだ内容が残っている。
    note.get_by_role("button", name="設定する").click()
    page.locator("[data-setup='parent:母']").wait_for()
    report.check(
        page.locator("[data-setup='parent:母']").get_attribute("aria-pressed") == "true",
        "前に選んだ内容が消えた",
    )
    context.close()


def husband_note(browser, out: Path, report: Report) -> None:
    """夫の3択とひとこと。ひとことは親の返事のカードの中だけに出て、症状の守りが効く。"""
    context = browser.new_context(
        viewport={"width": WIDTH, "height": HEIGHT},
        device_scale_factor=2,
        color_scheme="light",
        has_touch=True,
    )
    page = context.new_page()
    page.goto(BASE + "/", wait_until="networkidle")
    start_home(page, "家庭Aで試す")
    to_now(page)
    code = page.evaluate("location.hash.slice(1)")
    dismiss_save_link(page)
    version = request("GET", "/api/board", code)["version"]
    request("PATCH", "/api/board/items/a-02", code, {"version": version, "status": "頼んだ"})
    husband = context.new_page()
    husband.goto(f"{BASE}/m#{code}/a-02", wait_until="networkidle")
    husband.get_by_role("button", name="時間を相談したい", exact=True).wait_for()
    # 結果を入れる前は、夫の画面に次の予定の帯を出さない（条件つきの候補を確定の予定のように見せない。医療の線）。
    report.check(
        husband.locator("[data-one-next]").count() == 0, "結果の前に夫の画面に次の予定が出ている"
    )
    report.check(first_answer(husband).count() == 1, "1つ目の答えが無い")
    for name in ("時間を相談したい", "むずかしい"):
        report.check(
            husband.get_by_role("button", name=name, exact=True).count() == 1, f"{name}が無い"
        )
    report.check(
        husband.get_by_role("button", name="手配する").count() == 0, "前の選択肢が残っている"
    )
    send = husband.get_by_role("button", name="この返事を送る")
    report.check(send.is_disabled(), "選ぶ前に送れる")
    box = husband.get_by_role("textbox", name="ひとこと")
    report.check(box.get_attribute("maxlength") == "40", "ひとことの上限が40字でない")
    page.wait_for_timeout(1100)
    reply_one(husband, "持っていく", "19時ごろに持っていくよ")
    husband.get_by_text(re.compile("「(持っていく|引き受ける)」と送りました")).wait_for()
    inspect(husband, "one-note-sent", out, report)
    # 親の画面：返事のカードの中だけに出る。やりとりの一覧に行として出さない。
    page.reload(wait_until="networkidle")
    card = page.locator("[data-now-reply]")
    card.wait_for()
    report.check("19時ごろに持っていくよ" in card.inner_text(), "ひとことがカードに無い")
    inspect(page, "now-reply-note", out, report)
    to_parts(page)
    open_parts(page)
    report.check(
        "19時ごろに持っていくよ" not in page.locator("main").inner_text(),
        "ひとことが一覧に出ている",
    )
    # 台帳では別の項目で、夫の発言。経緯の語には文を入れない。
    board = request("GET", "/api/board", code)
    notes = [item for item in board["items"] if item["refers_to"] == "a-02"]
    report.check(len(notes) == 1 and notes[0]["speaker"] == "夫", f"ひとことの項目が違う: {notes}")
    labels = [event["label"] for item in board["items"] for event in item["events"]]
    report.check(not any("19時ごろ" in label for label in labels), "経緯の語に文が入った")
    # 1つの用事に1回だけ。もう一度送ると置き換わる。症状の語には固定文。
    husband.get_by_role("button", name="答えを変える").click()
    page.wait_for_timeout(1100)
    reply_one(husband, "持っていく", "熱があるみたい")
    husband.get_by_text(re.compile("「(持っていく|引き受ける)」と送りました")).wait_for()
    board = request("GET", "/api/board", code)
    notes = [item for item in board["items"] if item["refers_to"] == "a-02"]
    report.check(len(notes) == 1, f"ひとことが増えた: {len(notes)}件")
    report.check(notes[0]["medical"] == "ask", "症状の語の守りが効いていない")
    to_now(page)
    page.reload(wait_until="networkidle")
    card.wait_for()
    report.check(MEDICAL_FIXED_TEXT in card.inner_text(), "症状のひとことに固定文が無い")
    context.close()


def free_search(browser, out: Path, report: Report) -> None:
    """④自由な検索欄。医療の問いは固定文だけで探さない。資料の文をそのまま出す。残すまで台帳は変わらない。"""
    context = browser.new_context(
        viewport={"width": WIDTH, "height": HEIGHT},
        device_scale_factor=2,
        color_scheme="light",
        has_touch=True,
    )
    page = context.new_page()
    page.goto(BASE + "/", wait_until="networkidle")
    start_home(page, "家庭Aで試す")
    to_now(page)
    code = page.evaluate("location.hash.slice(1)")
    dismiss_save_link(page)
    page.evaluate("localStorage.setItem('ensemble.consented', 'true')")
    page.reload(wait_until="networkidle")
    open_vault(page)
    page.locator("[data-search-box]").wait_for()
    # 切り替え（reading.LEDGER_SEARCH、環境変数 LEDGER_SEARCH）で、やりとりも探すか決まる。
    ledger = page.locator("main.vault").get_attribute("data-ledger") == "on"
    report.check(
        request("GET", "/api/board", code)["ledger_search"] is ledger, "切り替えと画面が合わない"
    )
    if ledger:
        free_search_ledger(page, code, out, report)
    else:
        free_search_docs(page, code, out, report)
    context.close()


def free_search_docs(page: Page, code: str, out: Path, report: Report) -> None:
    """切り替えが off のときの④（今までどおり、しおり・資料だけを探す）。"""
    # 資料が0枚でも検索欄と候補の語は出る（図の④）。探すと資料の追加へ案内する。
    report.check(page.locator("[data-search-chip]").count() == 3, "候補の語が3つでない")
    page.locator("[data-search-chip]").first.click()
    page.get_by_text("まだ資料がありません").wait_for()
    report.check(page.get_by_role("button", name="資料を追加する").count() == 1, "追加の入口が無い")
    # 資料を読ませる。
    page.get_by_role("button", name="資料を追加する").click()
    picture = {"name": "shiori.png", "mimeType": "image/png", "buffer": synthetic_png()}
    page.locator("input[type=file]").set_input_files([picture])
    page.get_by_role("button", name="入院のしおり", exact=True).click()
    page.get_by_role("button", name="AIに読ませて、後で探せるようにする").click()
    page.get_by_role("button", name="整理する", exact=True).click()
    page.get_by_text("資料を読み取りました").wait_for()
    from_menu(page, "しおり・資料から探す")
    box = page.locator("[data-search-box] input")
    box.wait_for()
    report.check("しおり・資料から探す" in page.locator("main").inner_text(), "見出しが違う")
    inspect(page, "search-box", out, report)
    before = request("GET", "/api/board", code)
    # 見つかるとき：資料の文と、種類と撮った日。
    box.fill("スリッパは使えますか")
    page.locator("[data-search-box] button").click()
    found = page.locator("[data-search-found]")
    found.wait_for()
    text_found = found.inner_text()
    report.check("スリッパはお控えください" in text_found, f"資料の文が無い: {text_found}")
    report.check("入院のしおり" in text_found, "資料の種類が無い")
    report.check(re.search(r"\d+/\d+", text_found) is not None, "撮った日が無い")
    report.check(
        found.get_by_role("button", name="元のページを見る").count() == 1, "元を見る入口が無い"
    )
    inspect(page, "search-found", out, report)
    report.check(request("GET", "/api/board", code) == before, "探しただけで台帳が変わった")
    # 医療の問い：固定文だけ。探さない。台帳も変えない。
    box.fill("薬を増やしたほうがいいですか")
    page.locator("[data-search-box] button").click()
    medical = page.locator("[data-search-medical]")
    medical.wait_for()
    report.check(medical.inner_text().strip() == MEDICAL_FIXED_TEXT, "固定文のままでない")
    report.check(found.count() == 0, "医療の問いで資料を出した")
    report.check(request("GET", "/api/board", code) == before, "医療の問いで台帳が変わった")
    inspect(page, "search-medical", out, report)
    # 語に当たる問いは「見つかりません」と言わない（QA Test）。
    for question in ("解熱剤はいつ渡される？", "CRPって何"):
        box.fill(question)
        page.locator("[data-search-box] button").click()
        page.wait_for_timeout(400)
        medical.wait_for()
        report.check(
            page.locator("[data-search-missing]").count() == 0, f"{question}が見つからない扱い"
        )
    # 見つからないとき：押したときだけ質問を残す。
    box.fill("駐車場はありますか")
    page.locator("[data-search-box] button").click()
    missing = page.locator("[data-search-missing]")
    missing.wait_for()
    report.check("しおりには見つかりませんでした" in missing.inner_text(), "見つからない文が無い")
    report.check(request("GET", "/api/board", code) == before, "残す前に台帳が変わった")
    inspect(page, "search-missing", out, report)
    missing.get_by_role("button", name="病院に聞くことに残す").click()
    missing.wait_for(state="detached")
    after = request("GET", "/api/board", code)
    added = after["items"][-1]
    report.check(
        (added["kind"], added["part"], added["text_original"])
        == ("質問", "病院", "駐車場はありますか"),
        f"残した項目が違う: {added}",
    )
    # 前に聞いたこと：切り替えが off でも、④の質問で残る（端末の中だけ）。
    report.check(
        page.locator("[data-asked-row]").count() >= 3, "off のときに前に聞いたことが残らない"
    )


def free_search_ledger(page: Page, code: str, out: Path, report: Report) -> None:
    """切り替えが on のときの④「質問する」。やりとりと資料から引用だけを出し、出どころを添える。

    医療の問いは固定文だけ。見つからないとき、病院のことだけ［病院に聞くことに残す］を出す。
    前に聞いたことは端末の中だけに残り、開くと保存した答えを出す。夫のリンクの画面では探さない。
    """
    sent: list = []
    page.on("request", lambda request_: sent.append(request_))
    main = page.locator("main.vault")
    report.check("質問する" in main.locator("h1").first.inner_text(), "題が「質問する」でない")
    report.check(
        "入れたやりとりと、しおり・資料の中から探します" in main.inner_text(), "lead が違う"
    )
    box = page.locator("[data-search-box] input")
    report.check(box.get_attribute("placeholder") == "夫は何時に来る？", "placeholder が違う")
    report.check(page.locator("[data-search-chip]").count() == 5, "候補の語が5つでない")
    report.check(
        page.get_by_role("button", name="前の質問", exact=True).count() == 1, "時計のボタンが無い"
    )
    report.check(
        page.locator("[data-asked-none]").inner_text() == "まだ質問していません",
        "前に聞いたことの空の文が無い",
    )
    report.check(
        "この端末の中だけに残ります" in page.locator("[data-asked]").inner_text(),
        "端末の中だけの説明が無い",
    )
    inspect(page, "search-ask-box", out, report)
    before = request("GET", "/api/board", code)

    def searched(response) -> bool:
        return response.url.endswith("/api/board/search")

    def ask(question: str | None = None, chip: str | None = None) -> None:
        """探して、答えが描かれるまで待つ（前の答えと取り違えないよう、応答を待ってから見る）。"""
        with page.expect_response(searched):
            if chip:
                page.locator(f"[data-search-chip='{chip}']").click()
            else:
                box.fill(question or "")
                page.locator("[data-search-box] button").click()
        page.locator(
            "[data-search-found], [data-search-missing], [data-search-medical]"
        ).first.wait_for()

    # 退院の問い：医師の言葉の候補日を引用し、見込みの札だけ。日数は出さない。資料は0枚でも探せる。
    ask("いつ退院できる？")
    found = page.locator("[data-search-found]")
    text_found = found.inner_text()
    report.check("見つかったこと" in text_found, "見出しが「見つかったこと」でない")
    report.check(
        "よければ木曜に帰れるかも。" in text_found, f"医師の言葉の引用が無い: {text_found}"
    )
    source = found.locator("[data-search-source]").first.inner_text()
    report.check(
        re.search(r"医師の言葉 \d{1,2}/\d{1,2} \d{1,2}:\d{2}", source) is not None,
        f"出どころの行が違う: {source}",
    )
    report.check(
        found.locator("[data-search-certainty]").inner_text() == "見込み", "見込みの札が無い"
    )
    report.check(re.search(r"\d+日(後|で|間)", text_found) is None, "日数を出した")
    report.check(
        found.get_by_role("button", name="やりとりで見る").count() == 1, "行を開く入口が無い"
    )
    body = next(r for r in reversed(sent) if r.url.endswith("/api/board/search")).post_data_json
    report.check(body.get("ledger") is True, "親の画面で ledger を送っていない")
    inspect(page, "search-ask-found", out, report)
    report.check(request("GET", "/api/board", code) == before, "探しただけで台帳が変わった")

    # 第三者の文は要約と分かるように。
    ask("上司から何て来てた？")
    source = found.locator("[data-search-source]").first.inner_text()
    report.check(source.startswith("上司の文（要約）"), f"要約の出どころが違う: {source}")
    # 候補の語（夫の返事）：家族の言葉。
    ask(chip="夫の返事")
    source = found.locator("[data-search-source]").first.inner_text()
    report.check(source.startswith("夫の言葉"), f"夫の出どころが違う: {source}")

    # 病院のことでなく見つからない：読むだけ（残すボタンを出さない）。
    ask(chip="上司に伝えたこと")
    missing = page.locator("[data-search-missing]")
    report.check("見つかりませんでした" in missing.inner_text(), "見つからない文が無い")
    report.check(
        missing.get_by_role("button", name="病院に聞くことに残す").count() == 0,
        "病院のことでないのに残すボタンが出た",
    )
    inspect(page, "search-ask-missing-other", out, report)

    # 医療の問い：語に当たるもの（AIを呼ばない）と、仕分けで医療の問いになるもの。固定文だけ。
    medical = page.locator("[data-search-medical]")
    for question in ("薬を増やしたほうがいいですか", "退院して大丈夫なの？"):
        ask(question)
        report.check(medical.inner_text().strip() == MEDICAL_FIXED_TEXT, "固定文のままでない")
        report.check(found.count() == 0 and missing.count() == 0, f"{question}で固定文以外を出した")
    inspect(page, "search-ask-medical", out, report)

    # 病院のことで見つからない：押したときだけ質問を残す。
    ask("駐車場はありますか")
    report.check(
        "病院に聞くことに残す" in missing.locator("[data-search-hospital]").inner_text(),
        "病院のことの案内が無い",
    )
    report.check(request("GET", "/api/board", code) == before, "残す前に台帳が変わった")
    inspect(page, "search-ask-missing-hospital", out, report)
    missing.get_by_role("button", name="病院に聞くことに残す").click()
    missing.wait_for(state="detached")
    added = request("GET", "/api/board", code)["items"][-1]
    report.check(
        (added["kind"], added["part"], added["text_original"])
        == ("質問", "病院", "駐車場はありますか"),
        f"残した項目が違う: {added}",
    )

    # 前に聞いたこと：新しい順。医療の問いは固定文の印だけ。開くと保存した答えをそのまま出す。
    rows = page.locator("[data-asked-row]")
    report.check(rows.count() == 7, f"前に聞いたことが{rows.count()}件")
    report.check(
        rows.first.locator(".asked-question").inner_text() == "駐車場はありますか", "新しい順でない"
    )
    calls = len([r for r in sent if r.url.endswith("/api/board/search")])
    page.get_by_role("button", name="前の質問", exact=True).click()
    page.wait_for_timeout(600)
    top = page.locator("[data-asked]").bounding_box()
    report.check(top is not None and top["y"] < HEIGHT, "時計で前に聞いたことへ移らない")
    medical_row = rows.filter(has_text="退院して大丈夫なの？")
    report.check(
        "答え：体調や治療のことには答えません" in medical_row.inner_text(), "医療の行の答えが違う"
    )
    medical_row.locator(".asked-head").click()
    report.check(
        medical_row.locator(".medical").inner_text().strip() == MEDICAL_FIXED_TEXT,
        "医療の行を開いて固定文が出ない",
    )
    stored = page.evaluate(f"localStorage.getItem('ensemble.asked.{code}')")
    report.check(MEDICAL_FIXED_TEXT not in stored, "医療の問いの答えの文を端末に残した")
    discharge = rows.filter(has_text="いつ退院できる？")
    report.check("答え：「よければ木曜に帰れるかも。」" in discharge.inner_text(), "答えの行が違う")
    report.check(
        re.search(r"医師の言葉 \S+ \S+・聞いた \d+/\d+ \d+:\d\d", discharge.inner_text())
        is not None,
        f"出どころと聞いた時刻の行が違う: {discharge.inner_text()}",
    )
    discharge.locator(".asked-head").click()
    report.check(
        "よければ木曜に帰れるかも。" in discharge.locator("[data-asked-body]").inner_text(),
        "開いても答えが出ない",
    )
    report.check(
        len([r for r in sent if r.url.endswith("/api/board/search")]) == calls,
        "開いただけでAIを呼び直した",
    )
    inspect(page, "search-ask-history", out, report)
    # 1件ずつ消せる（2度押し）。読み直しても残りは残る。
    medical_row.get_by_role("button", name="消す", exact=True).click()
    medical_row.get_by_role("button", name="もう一度押すと消します").click()
    report.check(rows.count() == 6, "前に聞いたことを消せない")
    page.reload(wait_until="networkidle")
    open_vault(page)
    page.locator("[data-search-box]").wait_for()
    report.check(rows.count() == 6, "前に聞いたことが端末に残らない")
    # ［もう一度聞く］：上の欄にその問いを入れて探す。
    rows.filter(has_text="いつ退院できる？").locator(".asked-head").click()
    with page.expect_response(searched):
        rows.filter(has_text="いつ退院できる？").get_by_role("button", name="もう一度聞く").click()
    page.locator("[data-search-found]").wait_for()
    report.check(box.input_value() == "いつ退院できる？", "もう一度聞くで欄に問いが入らない")
    report.check(rows.count() == 6, "聞き直しで同じ問いが2行になった")
    # やりとりで見る：メッセージのその行を開く。
    page.locator("[data-search-found]").get_by_role("button", name="やりとりで見る").click()
    row = page.locator("[data-item='a-07']")
    row.wait_for()
    page.wait_for_timeout(500)
    box_row = row.bounding_box()
    report.check(box_row is not None and 0 <= box_row["y"] < HEIGHT, "やりとりの行が見えない")

    # 資料の引用も同じ段に出る。出どころは資料の種類と撮った日、元のページを見られる。
    open_vault(page)
    page.get_by_role("button", name="資料を追加する").click()
    picture = {"name": "shiori.png", "mimeType": "image/png", "buffer": synthetic_png()}
    page.locator("input[type=file]").set_input_files([picture])
    page.get_by_role("button", name="入院のしおり", exact=True).click()
    page.get_by_role("button", name="AIに読ませて、後で探せるようにする").click()
    page.get_by_role("button", name="整理する", exact=True).click()
    page.get_by_text("資料を読み取りました").wait_for()
    open_vault(page)
    page.locator("[data-search-box]").wait_for()
    ask("スリッパは使えますか")
    doc = found.locator("[data-search-answer='doc']")
    report.check("スリッパはお控えください" in doc.inner_text(), "資料の文が無い")
    source = doc.locator("[data-search-source]").inner_text()
    report.check(
        re.search(r"^しおり（病院の資料） \d+/\d+$", source) is not None,
        f"資料の出どころが違う: {source}",
    )
    report.check(
        doc.get_by_role("button", name="元のページを見る").count() == 1, "元を見る入口が無い"
    )
    inspect(page, "search-ask-doc", out, report)

    # 夫がリンクから開いた画面：やりとりを探さず、前に聞いたことも出さない。
    page.evaluate(f"sessionStorage.setItem('ensemble.fromLink', '{code}')")
    page.reload(wait_until="networkidle")
    open_vault(page)
    page.locator("[data-search-box]").wait_for()
    report.check(main.get_attribute("data-ledger") == "off", "夫の画面でやりとりを探す語になった")
    report.check(
        "しおり・資料から探す" in main.locator("h1").first.inner_text(), "夫の画面の題が違う"
    )
    report.check(page.locator("[data-asked]").count() == 0, "夫の画面に前に聞いたことが出た")
    report.check(
        page.get_by_role("button", name="前の質問", exact=True).count() == 0,
        "夫の画面に時計のボタンが出た",
    )
    box.fill("いつ退院できる？")
    with page.expect_response(searched):
        page.locator("[data-search-box] button").click()
    page.wait_for_timeout(300)
    body = next(r for r in reversed(sent) if r.url.endswith("/api/board/search")).post_data_json
    report.check("ledger" not in body, "夫の画面で ledger を送った")
    inspect(page, "search-ask-from-link", out, report)
    page.evaluate("sessionStorage.removeItem('ensemble.fromLink')")

    # 「この記録を消す」で、前に聞いたことも端末から消える。
    page.reload(wait_until="networkidle")
    key = f"ensemble.asked.{code}"
    report.check(page.evaluate(f"localStorage.getItem('{key}')") is not None, "前の質問が無い")
    open_consent(page)
    page.get_by_role("button", name="この記録を消す").click()
    page.get_by_role("button", name="もう一度押すと消します").click()
    page.locator("[data-setup-screen]").wait_for()
    report.check(
        page.evaluate(f"localStorage.getItem('{key}')") is None, "消した記録の前の質問が残った"
    )


def stage_three(browser, out: Path, report: Report) -> None:
    """②これまでに入れたもの、⑪進捗、⑫使い方とルール。"""
    context = browser.new_context(
        viewport={"width": WIDTH, "height": HEIGHT},
        device_scale_factor=2,
        color_scheme="light",
        has_touch=True,
    )
    context.grant_permissions(["clipboard-read", "clipboard-write"], origin=BASE)
    page = context.new_page()
    page.goto(BASE + "/", wait_until="networkidle")
    start_home(page, "家庭Aで試す")
    to_now(page)
    dismiss_save_link(page)
    page.evaluate("localStorage.setItem('ensemble.consented', 'true')")
    page.reload(wait_until="networkidle")
    # ②入れる画面：これまでに入れたものと、貼り付け。
    page.evaluate("navigator.clipboard.writeText('貼り付けた文')")
    open_add(page)
    recent = page.locator("[data-recent]")
    recent.wait_for()
    report.check(recent.locator("time").count() >= 1, "これまでに入れたものに時刻が無い")
    page.get_by_role("button", name="貼り付け").click()
    page.wait_for_timeout(200)
    report.check(
        "貼り付けた文" in page.get_by_role("textbox").first.input_value(), "貼り付けられない"
    )
    report.check("音声" in page.locator("main").inner_text(), "音声の案内が無い")
    inspect(page, "add-recent", out, report)
    recent.get_by_role("button", name="すべて").click()
    page.locator("[data-part='職場']").wait_for()
    # ⑪進捗。
    tab(page, "進捗別")
    page.get_by_role("tab", name="進捗").click()
    table = page.locator("[data-part-table]")
    table.wait_for()
    rows = table.locator("[data-table-part]")
    report.check(rows.count() >= 3, f"表の行が少ない: {rows.count()}")
    report.check("病院" in table.inner_text() and "職場" in table.inner_text(), "相手が無い")
    report.check(table.locator("time").count() >= 1, "表に最後の時刻が無い")
    inspect(page, "part-table", out, report)
    table.locator("[data-table-part='職場']").click()
    page.locator("[data-party-filter='職場']").wait_for()
    report.check(
        page.locator(".bottom-nav [aria-current='page']").inner_text().strip() == "やること",
        "表の行を押すとメッセージから離れる",
    )
    # ⑫使い方・同意と線引き（ハンバーガー）とルール（設定のタブ）。2026-11-07 PO で分けた。
    from_menu(page, "使い方")
    howto = page.locator("[data-howto]")
    howto.wait_for()
    report.check(MEDICAL_FIXED_TEXT in howto.inner_text(), "使い方に固定文が無い")
    inspect(page, "howto", out, report)
    page.get_by_role("button", name="戻る", exact=True).click()
    page.get_by_role("button", name="同意と線引き", exact=True).click()
    consent = page.locator("[data-consent-rules]")
    consent.wait_for()
    report.check(
        "実在の人の名前や病名は入れないでください" in consent.inner_text(), "同意の全文が無い"
    )
    report.check(MEDICAL_FIXED_TEXT in consent.inner_text(), "同意と線引きに固定文が無い")
    inspect(page, "consent-rules", out, report)
    page.get_by_role("button", name="戻る", exact=True).click()
    close_menu(page)
    from_menu(page, "頼れる人と支援")
    rules = page.locator("[data-rules]")
    rules.wait_for()
    inspect(page, "rules", out, report)
    context.close()


def task_list(browser, out: Path, report: Report) -> None:
    """やること（タスク管理 v3.6、2026-11-08 本人の決定）。メッセージは「やること」から開く。
    分類（病院の中で／届ける・来てもらう／病院の外で）、要約の1行、足す（AIなし）、期限を過ぎた印、
    知らせ（親が決めた時刻を過ぎたら「いま」の一番上。閉じるとこの端末で消える）、いまの「やること」の行。"""
    context = browser.new_context(
        viewport={"width": WIDTH, "height": HEIGHT},
        device_scale_factor=2,
        color_scheme="light",
        has_touch=True,
    )
    made = json.loads(
        urllib.request.urlopen(
            urllib.request.Request(
                BASE + "/api/boards",
                data=json.dumps({"home": "home_a"}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
        ).read()
    )
    code = made["code"]
    page = context.new_page()
    page.goto(f"{BASE}/b#{code}", wait_until="networkidle")
    to_now(page)
    dismiss_save_link(page)
    # メッセージは「やること」から開く。
    nav(page, "やること")
    switch = page.locator("[data-messages-switch]")
    switch.wait_for()
    report.check(
        switch.get_by_role("tab", name="一覧").get_attribute("aria-selected") == "true",
        "メッセージが「やること」から開かない",
    )
    tabs = [name.strip() for name in switch.get_by_role("tab").all_inner_texts()]
    report.check(tabs[:1] == ["一覧"], f"切り替えの先頭が「一覧」でない: {tabs}")
    board = request("GET", "/api/board", code)
    tasks = [
        item
        for item in board["items"]
        if item["kind"] == "行動"
        and not item["id"].startswith("n-")
        and not item["released"]
        and item["status"] not in ("完了", "届いた")
    ]
    for item in tasks:
        want = {"親": "1", "病院": "1", "家族": "2"}.get(item["part"], "3")
        row = page.locator(f"[data-task-group='{want}'] [data-item='{item['id']}']")
        report.check(row.count() == 1, f"分類が違う: {item['part']} {item['task_summary']}")
    report.check(page.locator("[data-task-sum]").count() == 1, "要約の1行が無い")
    inspect(page, "task-list", out, report)

    # 足す：用件だけで足せる。AIは呼ばない。期限は日付と時刻（過ぎた日にすると「期限を過ぎました」）。
    page.locator("[data-task-add]").click()
    form = page.locator("[data-task-add-form]")
    form.wait_for()
    report.check(
        form.locator("[data-task-deadline-input]").input_value() == ""
        and form.locator("[data-task-remind-input]").input_value() == "",
        "期限か知らせる時刻に初期値が入っている",
    )
    inspect(page, "task-add-form", out, report)
    form.get_by_label("用件").fill("限度額の書類を窓口に出す")
    # 日付は検査のサーバーの今日から決める（過ぎた期限・過ぎた知らせ・先の期限）。
    today = date.fromisoformat(request("GET", "/api/board", code)["today"])
    past = today - timedelta(days=1)
    ahead = today + timedelta(days=2)
    form.locator("[data-task-deadline-input]").fill(f"{past.isoformat()}T10:00")
    form.locator("[data-task-remind-input]").fill(f"{today.isoformat()}T00:00")
    form.locator("[data-task-submit]").click()
    page.get_by_text("やることを足しました").wait_for()
    board = request("GET", "/api/board", code)
    added = board["items"][-1]
    report.check(
        added["kind"] == "行動" and added["part"] == "親" and added["owner"] == "親",
        f"足したやることが違う: {added['kind']} {added['part']} {added['owner']}",
    )
    report.check(
        added["due"] == f"{past.month}/{past.day} 10:00",
        f"期限が親の選んだままでない: {added['due']}",
    )
    row = page.locator(f"[data-task-group='1'] [data-item='{added['id']}']")
    row.wait_for()
    report.check(row.locator("[data-task-overdue]").count() == 1, "期限を過ぎた印が無い")
    report.check(row.locator("[data-task-remind]").count() == 1, "知らせる時刻が行に無い")
    report.check(
        "期限を過ぎた 1" in page.locator("[data-task-sum]").inner_text(), "要約に期限を過ぎたが無い"
    )
    inspect(page, "task-overdue", out, report)

    # 知らせ：時刻を過ぎたら「いま」の一番上。自分の用事なので［もう一度送る］は無い。
    nav(page, "いま")
    band = page.locator("[data-remind-band]")
    band.wait_for()
    text = band.inner_text()
    report.check("『限度額の書類を窓口に出す』の時刻です" in text, f"知らせの文が違う: {text}")
    report.check(band.get_by_role("button", name="もう一度送る").count() == 0, "自分の用事に送る")
    report.check(band.get_by_role("button", name="済にする").count() == 1, "済にするが無い")
    report.check(
        page.locator("[data-now-line='tasks']").count() == 1, "いまに「やること」の行が無い"
    )
    inspect(page, "task-remind-band", out, report)
    band.get_by_role("button", name="閉じる").click()
    report.check(page.locator("[data-remind-band]").count() == 0, "閉じても知らせが残る")
    page.reload(wait_until="networkidle")
    page.locator("[data-next-step]").wait_for()
    report.check(page.locator("[data-remind-band]").count() == 0, "閉じた知らせが読み直すと戻る")
    # 家族の用事：［直す］で期限（日付と時刻で選ぶ）と知らせる時刻を入れる。知らせには［もう一度送る］。
    family = next(
        item
        for item in request("GET", "/api/board", code)["items"]
        if item["part"] == "家族"
        and item["kind"] == "行動"
        and item["status"] not in ("完了", "届いた")
        and not item["released"]
    )
    to_progress(page)
    row = page.locator(f"[data-item='{family['id']}']")
    reveal(row)
    row.locator(".row-main").click()
    row.get_by_role("button", name="直す", exact=True).click()
    panel = row.locator("[data-edit]")
    panel.wait_for()
    panel.locator("[data-edit-due-picker]").fill(f"{ahead.isoformat()}T17:00")
    report.check(
        panel.locator("[data-edit-due]").input_value() == f"{ahead.month}/{ahead.day} 17:00",
        "日付と時刻で選んだ期限が欄に入らない",
    )
    panel.locator("[data-edit-remind]").fill(f"{today.isoformat()}T00:00")
    inspect(page, "task-edit", out, report)
    panel.get_by_role("button", name="保存する").click()
    page.locator(f"[data-item='{family['id']}'] [data-task-remind]").wait_for()
    saved = next(i for i in request("GET", "/api/board", code)["items"] if i["id"] == family["id"])
    report.check(
        saved["due"] == f"{ahead.month}/{ahead.day} 17:00", f"期限が台帳に入らない: {saved['due']}"
    )
    weekday = "月火水木金土日"[ahead.weekday()]
    shown = page.locator(f"[data-item='{family['id']}']").inner_text()
    report.check(
        f"{ahead.month}/{ahead.day}（{weekday}）17:00まで" in shown,
        f"期限が「〇/〇（曜）〇:〇〇まで」で出ない: {shown}",
    )
    nav(page, "いま")
    band = page.locator("[data-remind-band]")
    band.wait_for()
    item_band = band.locator(f"[data-remind-item='{family['id']}']")
    report.check(
        "に頼んだ『" in item_band.inner_text() and "さんに" not in item_band.inner_text(),
        f"家族の知らせの文が違う: {item_band.inner_text()}",
    )
    report.check(
        item_band.get_by_role("button", name="もう一度送る").count() == 1,
        "家族の知らせに送るが無い",
    )
    inspect(page, "task-remind-family", out, report)
    # いまの行から、やることへ。
    page.locator("[data-now-line='tasks']").click()
    page.locator("[data-task-sum]").wait_for()
    context.close()


def own_group(browser, out: Path, report: Report) -> None:
    """同じ相手のまだ送っていない親の頼みごとは1通（PO の決定）。答えは用事ごと（Plan main の条件）。
    夫が返事を変えたら、親の「いま」のカードを置き換える（「夫が返事を変えました」）。"""
    context = browser.new_context(
        viewport={"width": WIDTH, "height": HEIGHT},
        device_scale_factor=2,
        color_scheme="light",
        has_touch=True,
    )
    context.grant_permissions(["clipboard-read", "clipboard-write"], origin=BASE)
    made = json.loads(
        urllib.request.urlopen(
            urllib.request.Request(
                BASE + "/api/boards",
                data=json.dumps({"home": "shoot_a"}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
        ).read()
    )
    code = made["code"]
    text = (
        "先生から「12日に採血。退院できるなら16日が候補、再検査なら17日」と言われた。"
        "16日のお迎えと、着替え（寝室の棚）をお願い。"
    )
    request("POST", "/api/board/intake", code, {"request_id": "own-group-1", "text": text})
    board = request("GET", "/api/board", code)
    asks = [
        item
        for item in board["items"]
        if item["part"] == "家族" and item["kind"] == "行動" and item["status"] == "頼みたい"
        and item["id"].startswith("i-")
    ]  # fmt: skip
    report.check(len(asks) == 2, f"夫への頼みごとが2件でない: {[a['task_summary'] for a in asks]}")
    page = context.new_page()
    page.goto(f"{BASE}/b#{code}", wait_until="networkidle")
    to_now(page)
    dismiss_save_link(page)
    line = page.locator("[data-next-step]").inner_text()
    report.check(
        "16日（退院の候補）のお迎えと着替えをお願いする" in line,
        f"いまの行がまとまっていない: {line}",
    )
    report.check("ほか" not in line, f"まとめた用事を「ほか」と数えている: {line}")
    page.locator("[data-now-actions]").get_by_role("button", name="送る文を見る").click()
    tabs = page.locator("[data-draft-tabs]")
    tabs.wait_for()
    report.check(tabs.get_by_role("tab").count() == 1, "夫の文が1通にまとまっていない")
    body = tabs.locator(".original").inner_text()
    report.check(
        body.count("16日のお迎えと、着替え（寝室の棚）をお願い。") == 1,
        f"同じ原文が2回並ぶ: {body}",
    )
    inspect(page, "own-group-draft", out, report)
    tabs.get_by_role("button", name="コピーする").click()
    copied = page.evaluate("navigator.clipboard.readText()")
    report.check(copied.count("16日のお迎えと") == 1 and "/m#" in copied, f"送る文が違う: {copied}")
    tabs.get_by_role("button", name="送った", exact=True).click()
    page.get_by_text("送りました").wait_for()
    board = request("GET", "/api/board", code)
    states = {item["id"]: item["status"] for item in board["items"]}
    report.check(
        all(states[a["id"]] == "頼んだ" for a in asks), f"全部が頼んだにならない: {states}"
    )
    # 結果の前の条件の段の案を進めておく（次の段に「17日 検査」が入り、直す前の夫の画面なら帯が出る状態）。
    doctor = next(
        item for item in board["items"] if item["part"] == "病院" and item.get("condition")
    )
    clause_ok, clause_test = "退院できるなら16日が候補", "再検査なら17日"
    request(
        "POST",
        "/api/board/branches",
        code,
        {
            "version": board["version"],
            "branch": {
                "id": "own-group-condition",
                "condition_item_id": doctor["id"],
                "candidate_days": [{"day": "16日", "certainty": "見込み"}],
                "outcomes": [
                    {
                        "label": "退院できる",
                        "clause": clause_ok,
                        "clause_item_id": doctor["id"],
                        "next": [{"day": "16日", "kind": "退院候補", "clause": clause_ok}],
                        "actions": [],
                    },
                    {
                        "label": "再検査になる",
                        "clause": clause_test,
                        "clause_item_id": doctor["id"],
                        "next": [{"day": "17日", "kind": "検査", "clause": clause_test}],
                        "actions": [],
                    },
                ],
            },
        },
    )
    after_branch = request("GET", "/api/board", code)
    report.check(
        after_branch["doctor"]["open"]
        and any(row["kind"] == "検査" for row in after_branch["doctor"]["paths"]),
        f"結果の前の次の段が無い: {after_branch['doctor']}",
    )
    # 夫：リンクは最初の用事を開く。答えは用事ごと。残りは「ほかのお願い」から。
    link = re.search(r"/m#\S+", copied)[0]
    husband = context.new_page()
    husband.goto(BASE + link, wait_until="networkidle")
    report.check(first_answer(husband).count() == 1, "夫の1つ目の答えが無い")
    # 結果の前（医師の言葉の分かれ道はある）の夫の画面に、次の予定の帯を出さない（acc34b1。QA の P2）。
    report.check(
        husband.locator("[data-one-next]").count() == 0, "結果の前に夫の画面に次の予定が出ている"
    )
    report.check(
        "11/17" not in husband.locator("body").inner_text(),
        "結果の前に夫の画面に再検査の日が出ている",
    )
    report.check(
        husband.get_by_role("button", name="むずかしい", exact=True).count() == 1,
        "むずかしい が無い",
    )
    report.check(
        "ほかのお願い（1件）" in husband.locator("body").inner_text(), "残りの用事が夫に見えない"
    )
    reply_one(husband, "持っていく", "19時ごろなら行けるよ")
    husband.get_by_text(re.compile("と送りました")).wait_for()
    inspect(husband, "own-group-husband", out, report)
    page.reload(wait_until="networkidle")
    to_now(page)
    band = page.locator("[data-reply-band]")
    band.wait_for()
    report.check("返事が届きました" in band.inner_text(), f"1回目の返事の帯: {band.inner_text()}")
    # ひとことだけ変える → 親のカードは置き換わり「夫が返事を変えました」。
    first_id = link.rsplit("/", 1)[1]
    page.wait_for_timeout(1100)
    request(
        "POST",
        f"/api/board/items/{first_id}/reply",
        code,
        {"answer": "引き受ける", "note": "会議が延びて20時半になる"},
    )
    page.reload(wait_until="networkidle")
    to_now(page)
    band.wait_for()
    report.check("返事を変えました" in band.inner_text(), f"変えた返事の帯: {band.inner_text()}")
    card = page.locator("[data-now-reply]")
    report.check("会議が延びて20時半になる" in card.inner_text(), "新しいひとことがカードに無い")
    report.check("19時ごろなら行けるよ" not in card.inner_text(), "前のひとことが重なっている")
    report.check(page.locator("[data-reply-band]").count() == 1, "返事のカードが重なっている")
    inspect(page, "own-group-changed", out, report)
    context.close()


def shoot_day(browser, out: Path, report: Report) -> None:
    """撮影の日付（水曜）で、よければ木曜は採血と同じ日。案の帯に取り消しの木曜と候補の火曜が出る。"""
    # 別のセッションのサーバーと取り合わないよう、空いている番号を取る。
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
        env = {**os.environ, "TMP": folder, "TEMP": folder, "PYTHONUTF8": "1"}
        env["AI_FAMILY_DAILY_LIMIT"] = env["AI_DAILY_LIMIT"] = "100000"
        env.pop("ANTHROPIC_API_KEY", None)
        server = subprocess.Popen(
            [
                sys.executable,
                "scripts/shoot_server.py",
                "--today",
                "2026-11-04",
                "--port",
                str(port),
            ],
            cwd=ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            for _ in range(60):
                try:
                    urllib.request.urlopen(base + "/health", timeout=2)
                    break
                except (urllib.error.URLError, OSError):
                    time.sleep(0.5)
            context = browser.new_context(
                viewport={"width": WIDTH, "height": HEIGHT},
                device_scale_factor=2,
                color_scheme="light",
                has_touch=True,
            )
            page = context.new_page()
            report.check(server.poll() is None, "撮影の日付のサーバーが起動していない")
            page.goto(base + "/", wait_until="networkidle")
            start_home(page, "家庭Aで試す")
            to_now(page)
            dismiss_save_link(page)
            page.evaluate("localStorage.setItem('ensemble.consented', 'true')")
            page.reload(wait_until="networkidle")
            clauses = page.locator("[data-clause]").all_inner_texts()
            good = next(text for text in clauses if "よければ木曜" in text)
            bad = next(text for text in clauses if "だめなら月曜" in text)
            report.check("11/5（木）" in good, f"よければ木曜が採血と同じ日でない: {good}")
            report.check("11/9（月）" in bad, f"だめなら月曜の日が違う: {bad}")
            # 端末の時計でなく、サーバーの今日。
            today_text = page.locator(".now-date").inner_text()
            report.check(today_text == "11月4日（水）", f"見出しの日付: {today_text}")
            page.locator("[data-after-entry]").click()
            page.locator("[data-option='1']").click()
            page.locator(".dock [data-primary]").click()
            plan_button(page).wait_for(timeout=15000)
            plan_button(page).click()
            page.locator("[data-decide-summary]").wait_for()
            lines = page.locator("[data-review-line]").all_inner_texts()
            report.check(
                any("11/5（木）" in line and "→" in line for line in lines),
                f"取り消しの日が違う: {lines}",
            )
            plan_detail(page)
            cells = page.locator(".week-strip .strip-day")
            cells.first.wait_for()
            strip = {
                cell.locator(".strip-label").inner_text(): cell.locator(".strip-mark").inner_text()
                for cell in cells.all()
            }
            report.check(strip.get("木") == "取り消し", f"帯の木曜に取消が無い: {strip}")
            report.check(strip.get("火") == "候補", f"帯の火曜に候補が無い: {strip}")
            press_accept(page)
            page.locator("[data-draft-tabs]").wait_for()
            page.locator(".bottom-nav").get_by_role("button", name="いま", exact=True).click()
            upcoming = page.locator("[data-upcoming]")
            upcoming.wait_for()
            report.check(
                "（火）" in upcoming.inner_text() and "見込み" in upcoming.inner_text(),
                f"いまに火曜の候補が無い: {upcoming.inner_text()}",
            )
            # ⑩の下書きの行は文の途中で切らない。
            nav(page, "カレンダー")
            page.locator("[data-cal-day='11/5']").click()
            page.locator("[data-week-selected]").wait_for()
            rows = page.locator("[data-week-selected] .week-row-text").all_inner_texts()
            # 日付のある行に、別の日の頼みごとの文を貼らない（火曜の空きの文を木曜に出さない）。
            report.check(
                not any("仮で押さえて" in text for text in rows), f"⑩に別の日の頼みごと: {rows}"
            )
            report.check(
                not any(text.endswith("ので") for text in rows), f"⑩の行が途中で切れている: {rows}"
            )
            context.close()
        finally:
            server.terminate()
            server.wait(timeout=15)


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
    start_home(page, "家庭Aで試す")
    to_parts(page)
    code = page.evaluate("location.hash.slice(1)")
    dismiss_save_link(page)
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
                # 木曜はどの曜日に開いても8日の帯の中。枠を測るので候補にする。
                "candidate_days": [
                    {"day": "木曜", "certainty": "見込み"},
                    {"day": "火曜", "certainty": "見込み", "cancelled": True},
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
        page.evaluate(f"document.documentElement.style.fontSize = '{87.5 * zoom / 100}%'")
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
    start_home(page, "家庭Aで試す")
    to_parts(page)
    code = page.evaluate("location.hash.slice(1)")
    dismiss_save_link(page)
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
    row.get_by_role("button", name="直す", exact=True).click()
    row.locator("[data-edit] textarea").last.fill("採血は問題ありませんでした")
    row.get_by_role("button", name="保存する").click()
    page.get_by_text("前の文で作った案は取り下げました").wait_for()
    inspect(page, "20e-edit-dropped-proposal", out, report)
    report.check(no_plan(page), "もとの文を直しても古い案が残る")

    # ［送ったと記録］の押し間違いは、帯からも、後で行の中からも戻せる。
    add_text(page, "今日の採血はだめでした")
    plan_button(page).wait_for(timeout=15000)
    plan_button(page).click()
    accept_plan(page)
    back_from_map(page)
    open_sends(page)
    send_row = page.locator("[data-send='家族']").first
    send_id = send_row.get_attribute("data-item")
    send_row.get_by_role("button", name="送った", exact=True).click()
    page.get_by_text("送りました").wait_for()
    moved = page.locator(f"[data-item='{send_id}']")
    inspect(page, "20f-asked-undo", out, report)
    dock_undo_button.click()
    page.get_by_text("元に戻しました").wait_for()
    moved.locator(".row-lead.send").wait_for()
    report.check(status_of(send_id) == "頼みたい", "送ったと記録を戻せない")
    if moved.locator(".row-body").count() == 0:
        moved.locator(".row-lead.send").click()
    moved.get_by_role("button", name="送った", exact=True).click()
    page.get_by_text("送りました").wait_for()
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
    start_home(page, "家庭Aで試す")
    to_parts(page)
    code = page.evaluate("location.hash.slice(1)")
    skip_candidate_check(page, code)
    dismiss_save_link(page)

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
    top = now_text(page)
    report.check("ほか" not in top and "送る文を見る" in top, f"前の案の文まで数えている: {top}")
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
    top = now_text(page)
    report.check("前の案の文が1件残っています" in top, f"前の案の文が残っていると言わない: {top}")
    report.check("送る文を見る" not in top, "前の案の文だけなのに下書きのボタンが出る")
    to_parts(page)
    older.locator(".row-lead.send").click()
    report.check("まだ送っていません" in older.inner_text(), "前の案の文の説明が無い")
    report.check(
        older.get_by_role("button", name="送った", exact=True).count() == 1, "前の案の文を送れない"
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
    page.wait_for_timeout(1100)  # 時刻は秒までなので、直前の変更より後にする。
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
    text = now_text(page)
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
    open_vault_list(page)
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
    unfold_vault_list(page)
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
    # ④は見出しの［戻る］をやめた。メニューから移る。
    open_trash(page)
    deleted_photo = page.locator("[data-trash-photo]")
    deleted_photo.wait_for()
    inspect(page, "36a-trash-photo", out, report)
    deleted_photo.get_by_role("button", name="元に戻す").click()
    page.get_by_text("元に戻しました").wait_for()
    page.get_by_role("button", name="戻る").click()
    open_vault_list(page)
    tiles.first.wait_for()
    report.check(tiles.count() == 2, "戻した写真が保管庫に無い")
    tiles.first.click()
    page.get_by_role("button", name="消す").click()
    page.get_by_role("button", name="もう一度押すと、ゴミ箱に入れます").click()
    tiles.first.wait_for()
    # ④は見出しの［戻る］をやめた。メニューから移る。
    open_trash(page)
    deleted_photo.get_by_role("button", name="完全に消す").click()
    deleted_photo.get_by_role("button", name="もう一度押すと、戻せなくなります").click()
    page.get_by_text("完全に消しました").wait_for()
    deleted_photo.wait_for(state="detached", timeout=5000)
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
        # 図の②：選ぶ札は無い。問いの形の文が病院への質問に仕分けられる。
        open_add(page)
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
    open_part(page, "病院")
    reveal(other)
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
        asked.get_by_role("button", name="しおり・資料から探す").count() == 0,
        "探すボタンが出ている",
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
    early.get_by_role("button", name="しおり・資料から探す").click()
    early.get_by_text("入院のしおりにありました").wait_for()

    # 検査結果へ変えたら、読み取った文を消して、探す対象から外す。
    open_vault_list(page)
    tile = page.locator(".photo-tile", has_text="入院のしおり").first
    tile.wait_for()
    tile.click()
    # 読み取った方の写真を見るまで順に見る。
    if page.get_by_text("読み取った文").count() == 0:
        page.get_by_role("button", name="戻る").click()
        page.locator(".photo-tile", has_text="入院のしおり").nth(1).click()
    page.get_by_text("読み取った文").wait_for()
    page.get_by_role("button", name="検査結果", exact=True).click()
    page.get_by_text("読み取った文").wait_for(state="detached")
    # ④は見出しの［戻る］をやめた。写真から戻ったら、下のナビで移る。
    page.get_by_role("button", name="戻る").click()
    to_parts(page)
    page.wait_for_timeout(500)
    report.check(
        page.get_by_role("button", name="しおり・資料から探す").count() == 0,
        "読み取った資料が無いのに探すボタンが出ている",
    )

    # あとから読ませることもできる。
    open_vault_list(page)
    page.locator(".photo-tile", has_text="入院のしおり").first.click()
    page.get_by_role("button", name="AIに読ませる", exact=True).click()
    page.get_by_text("読み取った文").wait_for()
    report.check(len(reads()) == 2, f"読み取りが{len(reads())}回")
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
    # 消した後は最初の画面（図の①「設定 1/2」）へ戻る。
    page.locator("[data-setup-screen]").wait_for()
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
    parser.add_argument("--only", default="", help="場面の関数名をコンマで（直すときだけ）")
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
            # 入院初日の職場への文（v3.5.11）の切り替え。公開の既定は off なので、検査では on にして
            # 新しい道を通す（off の画面は今と同じ）。外から WORK_FIRST を渡せば、その値で流す。
            "WORK_FIRST": os.environ.get("WORK_FIRST", "1"),
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
                    if args.only:
                        # 直している場面だけを流す。全検査の代わりにはならない。
                        names = args.only.split(",")
                        code = (
                            walk_through(page, args.out, report) if "walk_through" in names else ""
                        )
                        # 歩き通しのページを続けて使う場面（終わりの方の場面）。
                        tail = {
                            "withdraw_basis": lambda: withdraw_basis(page, code, args.out, report),
                            "photo_store": lambda: photo_store(
                                browser, page, code, args.out, report
                            ),
                            "reading_flow": lambda: reading_flow(page, code, args.out, report),
                        }
                        for name in names:
                            if name == "walk_through":
                                continue
                            # 1つの場面が止まっても、残りの場面を流して全部の落ちを見る。
                            try:
                                if name in tail:
                                    tail[name]()
                                else:
                                    globals()[name](browser, args.out, report)
                            except Exception as error:  # noqa: BLE001
                                lines = str(error).splitlines()
                                where = next((ln for ln in lines if "waiting for" in ln), "")
                                report.check(
                                    False, f"{name} が止まった: {lines[0][:120]} {where[:160]}"
                                )
                        browser.close()
                        total = report.passes + len(report.failures)
                        print(f"検査（--only）{total} 件／不合格 {len(report.failures)} 件")
                        return 1 if report.failures else 0
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
                    plan_and_drafts(browser, args.out, report)
                    work_first(browser, args.out, report)
                    shoot_day(browser, args.out, report)
                    own_home(browser, args.out, report)
                    husband_note(browser, args.out, report)
                    reply_states(browser, args.out, report)
                    free_search(browser, args.out, report)
                    stage_three(browser, args.out, report)
                    strip_frame(browser, args.out, report)
                    own_group(browser, args.out, report)
                    task_list(browser, args.out, report)
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
                    inspect(dark_page, "12-add-dark", args.out, report)
                    # 図の②：同意は最初の［整理する］で出る。
                    dark_page.get_by_role("textbox").fill("今日の採血はだめでした")
                    dark_page.get_by_role("button", name="整理する", exact=True).click()
                    dark_page.get_by_role("button", name="わかった").wait_for()
                    inspect(dark_page, "11-consent-dark", args.out, report)
                    dark_page.get_by_role("button", name="わかった").click()
                    plan_button(dark_page).click()
                    dark_page.get_by_role("button", name=re.compile("送る文を見る・")).wait_for()
                    inspect(dark_page, "20-decide-dark", args.out, report)
                    # 案を進めて、その場で順に送る。送ったら次の相手へ進み、件数が減る。
                    dark.grant_permissions(["clipboard-read", "clipboard-write"], origin=BASE)
                    accept_plan(dark_page)
                    dark_page.locator(".map-node").first.wait_for()
                    dark_page.get_by_text("送る文が3件あります").wait_for()
                    dark_page.wait_for_timeout(6000)
                    inspect(dark_page, "20a-map-dark", args.out, report)
                    dark_page.get_by_role("button", name="送る文を見る（3件）").click()
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
                    dark_page.get_by_role("button", name="送った", exact=True).click()
                    dark_page.get_by_role("heading", name="職場に伝える").wait_for()
                    dark_page.get_by_role("button", name="戻る").click()
                    dark_page.get_by_text("送る文が2件あります").wait_for()
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
                    start_home(shoot_page, "家庭Aで試す")
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
                    # 2026-11-07 本人の案d：前からの「退院の日の迎え」を外して5件。
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
                    start_home(b_page, "家庭Bで試す")
                    to_parts(b_page)
                    open_parts(b_page)
                    b_page.get_by_role("heading", name="支援").wait_for()
                    from_menu(b_page, "あなたと家族のこと")
                    b_page.locator("[data-home-cards] .card").first.wait_for()
                    # 2026-11-07 本人の決定：家庭Bは夫に頼めず、用事を頼めるのは近くの祖父母だけ。
                    report.check(
                        "平日の昼も夜も動ける" in b_page.locator("body").text_content(),
                        "家庭Bの祖父母の動ける時が出ていない",
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
