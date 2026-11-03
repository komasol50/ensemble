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
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"
WIDTH, HEIGHT = 360, 740
MIN_TAP, MIN_GAP, MIN_FONT, MIN_CONTRAST = 48, 8, 16, 4.5
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
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  const buttons = [...document.querySelectorAll('button, a[href]')].filter(visible);
  const rects = buttons.map((el) => {
    const r = el.getBoundingClientRect();
    return {text: el.innerText, top: r.top + scrollY, bottom: r.bottom + scrollY,
            left: r.left, right: r.right, h: r.height,
            primary: el.hasAttribute('data-primary'), docked: !!el.closest('.dock')};
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
  scrollTo(0, document.documentElement.scrollHeight);
  const top = dock.getBoundingClientRect().top;
  const others = [...document.querySelectorAll('main button')];
  const bottom = others.length
    ? Math.max(...others.map((el) => el.getBoundingClientRect().bottom)) : -Infinity;
  scrollTo(0, y);
  return bottom === -Infinity ? null : Math.round(top - bottom);
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
        columns == ["いま決めること", "週の予定", "病院", "家族", "保育園・預け先", "職場"],
        f"列の順が違う: {columns}",
    )

    # 週の表：マスに原文が出る。今日と、その先7日。
    rows = page.locator("table.week tbody tr")
    report.check(rows.count() == 8, f"週の表の行が{rows.count()}行")
    report.check("今日" in rows.first.inner_text(), "週の表の先頭が今日でない")
    week_text = page.locator("table.week").inner_text()
    report.check("明日の採血しだい" in week_text, "週の表に医師の言葉が無い")
    report.check("木曜の会議" in week_text, "週の表に職場の予定が無い")

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
    page.get_by_role("heading", name="職場").wait_for()
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
    item.get_by_text("完了").wait_for()
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
    report.check("返答待ち" in page.locator("[data-item='a-06']").inner_text(), "読み直していない")
    report.check("未確認" in question.inner_text(), "古い版の更新が通った")
    inspect(page, "9-board-conflict", out, report)
    question.get_by_role("button", name="済").click()
    question.get_by_text("答えあり").wait_for()

    # 消す：押すとすぐ消え、5秒の間は元に戻せる。
    def item_ids() -> set[str]:
        return {item["id"] for item in request("GET", "/api/board", code)["items"]}

    target = page.locator("[data-item='a-04']")
    target.get_by_role("button", name="消す").click()
    target.wait_for(state="detached")
    page.get_by_text("消しました").wait_for()
    inspect(page, "9a-board-undo", out, report)
    page.get_by_role("button", name="元に戻す").click()
    target.wait_for()
    report.check("a-04" in item_ids(), "元に戻しても台帳から消えた")
    page.wait_for_timeout(5500)
    report.check("a-04" in item_ids(), "元に戻したのに、時間が来て消えた")
    target.get_by_role("button", name="消す").click()
    target.wait_for(state="detached")
    page.wait_for_timeout(5500)
    report.check("a-04" not in item_ids(), "5秒たっても台帳から消えない")
    report.check(target.count() == 0, "消した項目が画面に戻った")

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
    report.check("原文は保存していません" in boss.inner_text(), "第三者の文の知らせが無い")
    report.check("どうしますか" not in boss.inner_text(), "第三者の原文が残っている")

    # 親だけの用事も画面から消えない。案にならない文でも、どこへ入ったかを知らせる。
    add_text(page, "売店で買う")
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
    page.get_by_role("button", name="戻る").click()
    page.get_by_role("button", name="確かめる").click()
    page.get_by_role("button", name="一部だけ変える").click()
    page.locator("[data-action='職場']").get_by_role("button", name="この案は使わない").click()
    inspect(page, "18-decide-partial", out, report)
    pressed = request("GET", "/api/board", code)
    report.check(pressed["branches"] == [], "押す前に案が台帳に入った")
    page.get_by_role("button", name="この案で進める").click()
    page.get_by_role("heading", name="まとめて送る").wait_for()
    inspect(page, "19-board-after", out, report)
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
    report.check("火曜の延長の空き" in copied, "コピーした文が違う")
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
    family_card.get_by_role("button", name="共有").click()
    family_card.get_by_role("button", name="頼んだにする").wait_for()
    text = page.evaluate("window.__shared.text")
    report.check("着替え3日分" in text, "共有した文に用件が無い")
    link = re.search(r"http\S+/m#\S+", text)
    report.check(link is not None, "共有した文に1件のリンクが無い")
    item_id = family_card.get_attribute("data-item")
    # LINEから戻ると開き直されることがある。［頼んだにする］は残る。
    page.reload(wait_until="networkidle")
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
    body = h_page.locator("body").inner_text()
    report.check("着替え3日分" in body, "1件の画面に用件が無い")
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
    """案の根拠の項目も消せて、スクロールした位置でも知らせが見える（実機で「消えない」の原因）。"""
    before = request("GET", "/api/board", code)["items"]
    sent = [i["id"] for i in before if i["id"].startswith("s-") and i["part"] == "家族"]
    other = [i["id"] for i in before if i["id"].startswith("s-") and i["part"] != "家族"]
    basis = page.locator("[data-item='a-02']")
    basis.scroll_into_view_if_needed()
    basis.get_by_role("button", name="消す").click()
    basis.wait_for(state="detached")
    note = page.get_by_text("これを根拠にした案も取り下げます")
    note.wait_for()
    box = note.bounding_box()
    report.check(0 <= box["y"] < HEIGHT, f"消した知らせが画面の外にある（{box['y']}）")
    inspect(page, "31-board-withdraw", out, report)
    page.get_by_role("button", name="元に戻す").click()
    basis.wait_for()
    basis.get_by_role("button", name="消す").click()
    basis.wait_for(state="detached")
    page.wait_for_timeout(5500)
    ids = {i["id"] for i in request("GET", "/api/board", code)["items"]}
    report.check("a-02" not in ids, "根拠の項目が消えていない")
    report.check(set(sent) <= ids, "送った連絡まで消えた")
    report.check(set(other) <= ids, "別の案の連絡まで消えた")


def remove_board(browser, page: Page, out: Path, report: Report) -> str:
    """このボードを消す。ほかの端末も見つからない表示になり、端末にも残らない。"""
    code = page.evaluate("location.hash.slice(1)")
    other = browser.new_context(viewport={"width": WIDTH, "height": HEIGHT})
    other_page = other.new_page()
    other_page.goto(f"{BASE}/b#{code}", wait_until="networkidle")
    other_page.get_by_role("heading", name="支援").wait_for()

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
        env = {**os.environ, "DATABASE_URL": f"sqlite:///{(Path(tmp) / 'check.db').as_posix()}"}
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
                    dark_page.get_by_role("heading", name="職場").wait_for()
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
                    withdraw_basis(page, code, args.out, report)

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
        server_log = log_path.read_text(encoding="utf-8")
    report.check("GET /api/board" in server_log, "サーバーの記録を読めていない")
    report.check(code not in server_log, "家族コードがサーバーの記録に出ている")
    report.check(removed_code not in server_log, "消したボードのコードがサーバーの記録に出ている")
    print(f"検査 {report.passes + len(report.failures)} 件／不合格 {len(report.failures)} 件")
    return 1 if report.failures else 0


if __name__ == "__main__":
    sys.exit(main())
