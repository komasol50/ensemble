"""週の表。台帳の文に書かれた曜日と日付から描く。AIを呼ばず、予定を作らない。

マスの中は原文のまま。言い換えると、誰も言っていないことが表に出るため。
曜日は、その文が記録された日から見た次の曜日として置く。
"""

import re
from datetime import date, datetime, timedelta
from typing import Any

from .guards import CONDITION_WORDS, extract_date_tokens, unmask_aliases
from .ledger import Branch, Certainty, Family, Item, Medical, NextKind, Part, Status

COLUMNS = ("退院", "病院", "家・園", "仕事")
PART_COLUMN = {
    Part.HOSPITAL: 1,
    Part.CHILDCARE: 2,
    Part.SUPPORT: 2,
    Part.FAMILY: 2,  # 日付の入った家族の用事だけ。病室に誰がいるかの予定は描かない。
    Part.WORK: 3,
}
DAYS_AFTER_TODAY = 7
WEEKDAYS = "月火水木金土日"
FINISHED = (Status.DELIVERED, Status.DONE)
RELATIVE_DAYS = {
    "今日": 0,
    "今朝": 0,
    "今夜": 0,
    "今晩": 0,
    "明日": 1,
    "あした": 1,
    "明後日": 2,
    "あさって": 2,
}
# 「、」でも切るが、「月、火の空き」のように曜日を並べた所では切らない。
_CLAUSE = re.compile(rf"[。\n！？!?]|、(?![{WEEKDAYS}][・、の曜])")
_SPEAKER = re.compile(r"^[^：:]{1,8}[：:]\s*")
_MONTH_DAY = re.compile(r"(\d+)/(\d+)")
_DAY_OF_MONTH = re.compile(r"(\d+)日")


# 引用の外の言葉（「先生から「」「」と言われた」）は節から外す。親の言葉で、医師の言葉ではない。
# 引用が節の中で閉じるときは触らない（「明日の採血」しだい、の「しだい」は待つ日の語）。
_QUOTE_HEAD = re.compile(r"^[^「」『』]{0,10}[「『]")
_QUOTE_TAIL = re.compile(r"[」』][^「」『』]{0,10}$")


def _outside_quote(clause: str) -> str:
    opens = clause.count("「") + clause.count("『")
    closes = clause.count("」") + clause.count("』")
    if opens and not closes:
        clause = _QUOTE_HEAD.sub("", clause)
    elif closes and not opens:
        clause = _QUOTE_TAIL.sub("", clause)
    return clause.strip()


def split_clauses(text: str) -> list[str]:
    clauses = (_outside_quote(clause) for clause in _CLAUSE.split(_SPEAKER.sub("", text)))
    return [clause for clause in clauses if clause]


def _weekday_date(index: int, base: date, tokens: set[str]) -> date:
    offset = (index - base.weekday()) % 7
    if "今週" in tokens:
        return base + timedelta(days=offset)
    next_monday = base + timedelta(days=7 - base.weekday())
    if "再来週" in tokens:
        return next_monday + timedelta(days=7 + index)
    if "来週" in tokens:
        return next_monday + timedelta(days=index)
    # 同じ曜日は、言った日ではなく次の週を指すことが多い。
    return base + timedelta(days=offset or 7)


def _month_day_date(month: int, day: int, base: date) -> date | None:
    try:
        found = date(base.year, month, day)
        return found if found >= base else date(base.year + 1, month, day)
    except ValueError:
        return None


def _day_of_month_date(day: int, base: date) -> date | None:
    try:
        found = base.replace(day=day)
        if found >= base:
            return found
        next_month = (base.replace(day=1) + timedelta(days=32)).replace(day=1)
        return next_month.replace(day=day)
    except ValueError:
        return None


def dates_in(text: str, base: date) -> list[date]:
    """文に書かれた日を、base から見た日付にする。書かれていなければ空。"""
    tokens = extract_date_tokens(text)
    found: list[date | None] = []
    for token in tokens:
        if token in RELATIVE_DAYS:
            found.append(base + timedelta(days=RELATIVE_DAYS[token]))
        elif token.endswith("曜"):
            found.append(_weekday_date(WEEKDAYS.index(token[0]), base, tokens))
        elif match := _MONTH_DAY.fullmatch(token):
            found.append(_month_day_date(int(match[1]), int(match[2]), base))
        elif match := _DAY_OF_MONTH.fullmatch(token):
            found.append(_day_of_month_date(int(match[1]), base))
    return sorted({day for day in found if day is not None})


def _recorded_day(item: Item | None, today: date) -> date:
    """文が記録された日。無いときと、未来の日付のときは今日として扱う。"""
    if item is None or not item.recorded_at:
        return today
    try:
        return min(datetime.fromisoformat(item.recorded_at).date(), today)
    except ValueError:
        return today


def _candidate_cell(certainty: Certainty, cancelled: bool) -> dict[str, str]:
    if cancelled:
        return {"text": "取り消し", "style": "cancelled"}
    if certainty == Certainty.CONFIRMED:
        return {"text": "確定", "style": "confirmed"}
    return {"text": "候補", "style": "likely"}


def _released_ids(branch: Branch | None) -> set[str]:
    if branch is None:
        return set()
    return {
        item_id
        for outcome in branch.outcomes
        for action in outcome.actions
        for item_id in action.release
    }


def calendar_days(today: date, weeks: int = 4) -> list[date]:
    """カレンダーの日。今週の月曜から。前後の週へ動けるよう、表示より少し長く持つ。"""
    monday = today - timedelta(days=today.weekday())
    return [monday + timedelta(days=offset) for offset in range(weeks * 7)]


def week_view(
    family: Family,
    today: date,
    branch: Branch | None = None,
    days: list[date] | None = None,
) -> dict[str, Any] | None:
    """画面へ渡す週の表。描くものが無ければ None。

    branch は親が押す前の案。無ければ台帳の最新の案を使う。
    """
    latest = branch or (family.branches[-1] if family.branches else None)
    by_id = {item.id: item for item in family.items}
    rows = days or [today + timedelta(days=offset) for offset in range(DAYS_AFTER_TODAY + 1)]
    cells: dict[date, list[list[dict[str, str]]]] = {day: [[] for _ in COLUMNS] for day in rows}
    others: list[str] = []
    has_candidate: set[date] = set()

    def put(day: date, column: int, cell: dict[str, str]) -> None:
        if day not in cells or cell in cells[day][column]:
            return
        # 同じ日に同じ項目は1回だけ。別の項目の文は別の行のまま。
        item_id = cell.get("item_id")
        if item_id and any(entry.get("item_id") == item_id for entry in cells[day][column]):
            return
        cells[day][column].append(cell)

    original = ""
    condition_base = today
    if latest is not None:
        condition = _source_of(by_id, latest)
        condition_base = _recorded_day(condition, today)
        original = (condition.text_original or "") if condition else ""
        for candidate in latest.candidate_days:
            cell = _candidate_cell(candidate.certainty, candidate.cancelled)
            days = _same_words_day(candidate.day, original, condition_base)
            if not days:
                # 曜日にならない候補日（「来週前半」など）も、隠さずに表の外へ出す。
                others.append(f"{candidate.day}（{cell['text']}）")
            for day in days:
                # 表より先の候補日も隠さない（月・日・木〜土に開くと火曜が表の外になる）。
                if day > rows[-1]:
                    others.append(f"{_day_text(day)}（{cell['text']}）")
                put(day, 0, cell)
                has_candidate.add(day)

    released = _released_ids(latest)
    tests = _next_test_days(by_id, latest, today)
    for item in family.items:
        column = PART_COLUMN.get(item.part)
        if column is None or item.status in FINISHED or item.medical == Medical.ASK:
            continue
        base = _recorded_day(item, today)
        where = {"item_id": item.id, "part": str(item.part.value)}
        style = "released" if item.id in released else "plain"
        text = item.text_original or item.task_summary or ""
        if column == 1:
            # 「12日に採血」のように言い切った検査の日。「再検査なら17日」は案の先の日として上で読む。
            for clause, days in clause_days(text, base):
                word = _test_word(clause)
                if word and not any(head in clause for head in _CONDITIONAL):
                    for day in days:
                        tests.setdefault(day, word)
        for clause, days in clause_days(text, base):
            # 案から作った連絡の曜日は、医師の言葉の同じ曜日の節と同じ日に置く。
            # 下書きの文だけで読むと、木〜月に開いたとき候補日より1週早い日に出る（QA Test）。
            if item.id.startswith(SENT_PREFIX) and original and _only_weekdays(clause):
                days = _clause_match(clause, original, condition_base) or days
            for day in days:
                # 退院候補の日は、医師の同じ言葉を病院の列に重ねて出さない。
                if column == 1 and day in has_candidate:
                    continue
                put(day, column, {"text": clause, "style": style, **where})
        if item.due:
            for day in dates_in(item.due, base):
                put(day, column, {"text": item.task_summary or text, "style": style, **where})

    if not others and not any(cell for row in cells.values() for cell in row):
        return None

    def shown(row: list[list[dict[str, str]]]) -> list[list[dict[str, str]]]:
        return [
            [{**cell, "text": unmask_aliases(cell["text"], family.aliases)} for cell in column]
            for column in row
        ]

    return {
        "columns": list(COLUMNS),
        "rows": [
            {
                "label": "今日" if day == today else WEEKDAYS[day.weekday()],
                "today": day == today,
                "date": f"{day.month}/{day.day}",
                "weekday": WEEKDAYS[day.weekday()],
                "cells": shown(cells[day]),
                # 図の⑩：年月の見出しと、マスの印（語は種類から決める）。
                "iso": day.isoformat(),
                "mark": _day_mark(cells[day], tests.get(day)),
            }
            for day in rows
        ],
        "others": [unmask_aliases(text, family.aliases) for text in others],
        "discharge": _undecided_discharge(latest, original, condition_base, family.aliases),
    }


MARK_MAX_CHARS = 3  # マスの語。長い語はマスに収まらない（文は下の「その日の予定」に出す）。
ERRAND_COLUMNS = (2, 3)  # 家・園、仕事。用件の語は読めないので、マスでは点で出す。


def _test_word(clause: str) -> str | None:
    """節にある検査の語（再検査、採血、検査）。原文の語のまま。"""
    return next((word for word in _TEST_WORDS if word in clause), None)


def _next_test_days(by_id: dict[str, Item], branch: Branch | None, today: date) -> dict[date, str]:
    """案の先の検査の日（NextKind の検査）。日は next_rows と同じく医師の言葉の節から読む。"""
    found: dict[date, str] = {}
    if branch is None:
        return found
    for outcome in branch.outcomes:
        source = by_id.get(outcome.clause_item_id or "")
        if source is None:
            continue
        base = _recorded_day(source, today)
        original = source.text_original or ""
        days_of = dict(clause_days(original, base))
        for step in outcome.next:
            if step.kind != NextKind.TEST:
                continue
            days = days_of.get(step.clause) or _same_words_day(step.day, original, base)
            if days:
                day = days[-1 if len(days) > 1 else 0]
                found.setdefault(day, _test_word(step.clause) or str(NextKind.TEST.value))
    return found


def _day_mark(row: list[list[dict[str, str]]], test: str | None) -> dict[str, str] | None:
    """マスの印。退院の候補日が先で、次に検査の日。用件だけの日は印を付けない（画面が点を出す）。

    取り消しになった候補日に用件が残っていれば「見直し」（黄）。用件が無ければ「取消」。
    """
    candidates = row[0]
    if candidates:
        if all(cell["style"] == "cancelled" for cell in candidates):
            if any(row[column] for column in ERRAND_COLUMNS):
                return {"text": "見直し", "tone": "review"}
            return {"text": "取消", "tone": "cancelled"}
        if any(cell["style"] == "confirmed" for cell in candidates):
            return {"text": "確定", "tone": "confirmed"}
        return {"text": "候補", "tone": "likely"}
    if test:
        return {"text": test[:MARK_MAX_CHARS], "tone": "test"}
    return None


def _undecided_discharge(
    branch: Branch | None, original: str, base: date, aliases: dict[str, str]
) -> dict[str, Any] | None:
    """退院の候補日が1つも残っていないときだけ「退院 未定」と以前の候補日（Plan main）。

    候補日が初めから無い分かれ道では出さない（以前の候補が無いので）。
    """
    if branch is None or not branch.candidate_days:
        return None
    if not all(candidate.cancelled for candidate in branch.candidate_days):
        return None
    previous: list[str] = []
    for candidate in branch.candidate_days:
        days = _same_words_day(candidate.day, original, base)
        text = _day_text(days[0]) if days else unmask_aliases(candidate.day, aliases)
        if text not in previous:
            previous.append(text)
    return {"undecided": True, "previous": previous}


# ⑤の選択肢。条件の語のある節を1つにし、「それで」で始まる節は前の節につなげる。
_CONDITIONAL = ("なら", "たら", "れば")
_CHAINED = ("それで", "その後")
_RESULT_HEAD = re.compile(r"^.{1,12}?(?:ければ|なら|たら|れば)(.+)$")
_WAITING_ON = re.compile(r"しだい$|次第$")
# よくない結果の節。「問題なければ」はよいほうなので「なければ」では見ない。
_NOT_GOOD = (
    "だめ",
    "ダメ",
    "悪け",
    "悪かったら",
    "下がらな",
    "上がらな",
    "よくなければ",
    "良くなければ",
    "もう一回",
    "もう一度",
    "再検査",
    "再度",
    "やり直",
)
_WEEKDAY = re.compile(rf"[{WEEKDAYS}]曜")
# 決まった言い換えの一覧。これに合わない言い回しでは選択肢を出さない（AIに作らせない）。
_SAID_FORMS = (
    (re.compile(r"とって$"), "とる"),
    (re.compile(r"して$"), "する"),
    (re.compile(r"るかも$"), "そう"),
    (re.compile(r"るかな$"), "そう"),
)


def _day_text(day: date) -> str:
    return f"{day.month}/{day.day}（{WEEKDAYS[day.weekday()]}）"


def waiting_day(text: str, base: date) -> date | None:
    """「明日の採血しだい」の日。条件の節の曜日は、この日より後として読む。"""
    for clause in split_clauses(text):
        if _WAITING_ON.search(clause):
            days = dates_in(clause, base)
            if days:
                return days[0]
    return None


def _after(day: date, floor: date | None, *, same_day: bool = False) -> date:
    """floor より後の日にする。same_day なら同じ日も認める。"""
    while floor is not None and (day < floor or (day == floor and not same_day)):
        day += timedelta(days=7)
    return day


def _clause_match(words: str, text: str, base: date) -> list[date] | None:
    """words の日の語を全部含む、医師の言葉の節の日。無ければ None。"""
    tokens = extract_date_tokens(words)
    for clause, days in clause_days(text, base):
        if days and tokens and tokens <= extract_date_tokens(clause):
            return days
    return None


def _same_words_day(words: str, text: str, base: date) -> list[date]:
    """候補日の語を、医師の言葉の同じ語の節と同じ日に読む。節の日と候補日を食い違わせない。"""
    matched = _clause_match(words, text, base)
    if matched is not None:
        return matched
    waited = waiting_day(text, base)
    return [_after(day, waited, same_day=True) for day in dates_in(words, base)]


SENT_PREFIX = "s-"  # 案から作った連絡の項目（ledger.sent_item_id）


def _only_weekdays(text: str) -> bool:
    """日の語が曜日だけか。「明日」は書いた日から数えるので、医師の言葉の節に合わせない。"""
    tokens = extract_date_tokens(text)
    return bool(tokens) and all(token.endswith("曜") for token in tokens)


def _source_of(by_id: dict[str, Item], branch: Branch) -> Item | None:
    """案の条件の項目。結果の項目を指しているときは、もとの医師の言葉。"""
    source = by_id.get(branch.condition_item_id)
    if source is not None and source.refers_to in by_id:
        source = by_id[source.refers_to]
    return source


def candidate_rows(family: Family, branch: Branch, today: date) -> list[dict[str, Any]]:
    """候補日の行。日は医師の言葉の同じ語の節と同じ日に読む。"""
    by_id = {item.id: item for item in family.items}
    source = _source_of(by_id, branch)
    base = _recorded_day(source, today)
    text = unmask_aliases(source.text_original or "", family.aliases) if source else ""
    rows = []
    for candidate in branch.candidate_days:
        days = _same_words_day(candidate.day, text, base)
        rows.append(
            {
                "day": _day_text(days[0]) if days else candidate.day,
                "certainty": str(candidate.certainty.value),
                "cancelled": candidate.cancelled,
            }
        )
    return rows


def clause_days(text: str, base: date) -> list[tuple[str, list[date]]]:
    """節ごとの日。曜日は話の順に読む（「だめなら月曜」は採血の後、「それで火曜」はその後）。"""
    waited = waiting_day(text, base)
    found: list[tuple[str, list[date]]] = []
    previous: date | None = None
    good: date | None = None
    for clause in split_clauses(text):
        days = dates_in(clause, base)
        floor = None
        # よい結果の先は採血と同じ日でもよい（水曜に聞く「明日の採血、よければ木曜」）。
        # よくない結果は、よい結果の日より後。話の順を逆に読まない。その続きはさらに後。
        same_day = False
        if clause.startswith(_CHAINED):
            floor = previous
        elif any(word in clause for word in _CONDITIONAL):
            if any(word in clause for word in _NOT_GOOD):
                floor = max((day for day in (waited, good) if day), default=None)
            else:
                floor = waited
                same_day = True
        if _WEEKDAY.search(clause):
            days = [_after(day, floor, same_day=same_day) for day in days]
        if days:
            previous = days[-1]
            if same_day:
                good = days[-1]
        found.append((clause, days))
    return found


def said_certainty(said: str) -> str:
    """医師がまだ言い切っていない文は見込みで入れる。確定で入れるとAIが候補日を確定にする。"""
    return "見込み" if "そう" in said else "確定"


def _said_text(clause: str) -> str | None:
    """押した選択肢から、親が入れる文を作る。形が合わなければ None（入力欄へ）。

    良し悪しを言わず、言われたことだけを書く。節をそのまま引くと、条件の語が残って
    確かさの守りで「見込み」に下がり、案が確認だけになる。
    """
    head = _RESULT_HEAD.match(clause)
    if head is None:
        return None
    for form, ending in _SAID_FORMS:
        if form.search(head[1]):
            said = f"{form.sub(ending, head[1])}と言われた"
            usable = extract_date_tokens(said) and not any(w in said for w in CONDITION_WORDS)
            return said if usable else None
    return None


# 条件の語が退院・検査のときの、決まった形の選択肢（図の⑤）。語は原文から取る。
_KIND_HEAD = re.compile(r"^(.{1,12}?)(?:なら|たら|れば)")
_TEST_WORDS = ("再検査", "採血", "検査")
_DISCHARGE_TITLE = "退院の予定になった"
_UNDECIDED = "退院日は未定"


def _first_date_word(clause: str) -> str | None:
    """節の中で最初に出る日の語（原文の表現のまま）。"""
    found = [
        (match.start(), match[0])
        for match in re.finditer(rf"\d+/\d+|\d+日|[{WEEKDAYS}]曜|明日|明後日|あさって", clause)
    ]
    return min(found)[1] if found else None


def _kind_option(clause: str) -> dict[str, Any] | None:
    """「退院できるなら16日が候補」→ 退院の予定になった、「再検査なら17日」→ 再検査になった。

    退院は、医師が候補と言った日なので見込みのまま（Plan main）。入れる文は「◯◯と言われた」。
    """
    head = _KIND_HEAD.match(clause)
    day_word = _first_date_word(clause)
    if head is None or day_word is None:
        return None
    if "退院" in head[1]:
        said = f"{day_word}に{_DISCHARGE_TITLE}と言われた"
        return {"kind": "退院", "title": _DISCHARGE_TITLE, "result": said, "certainty": "見込み"}
    word = next((word for word in _TEST_WORDS if word in head[1]), None)
    if word is None:
        return None
    title = f"{word}になった"
    return {
        "kind": "検査",
        "title": title,
        "result": f"{day_word}に{title}と言われた",
        "certainty": "確定",
    }


def after_title(text: str) -> str | None:
    """⑤の見出し。条件でない節にある検査の語で「採血のあと」。無ければ None（決まった見出し）。"""
    for clause in split_clauses(text):
        if any(word in clause for word in _CONDITIONAL):
            continue
        word = next((word for word in _TEST_WORDS if word in clause), None)
        if word:
            return f"{word}のあと"
    return None


def result_options(text: str, base: date) -> list[dict[str, Any]]:
    """結果の選択肢。2つ作れないときは空（原文の全文と入力欄だけを出す）。"""
    options: list[dict[str, Any]] = []
    for clause, days in clause_days(text, base):
        shown = [_day_text(day) for day in days]
        if options and clause.startswith(_CHAINED):
            # つないだ節も原文のまま。区切りの記号を書き換えない。
            last = options[-1]
            start = text.find(last["label"])
            end = text.find(clause, start) + len(clause)
            last["label"] = text[start:end]
            last["days"] = [*last["days"], *shown]
            continue
        if any(word in clause for word in _CONDITIONAL) and days:
            kind = _kind_option(clause)
            if kind is not None:
                options.append({"label": clause, "days": shown, **kind})
                continue
            result = _said_text(clause)
            if result is None:
                return []
            options.append(
                {
                    "label": clause,
                    "days": shown,
                    "result": result,
                    "certainty": said_certainty(result),
                }
            )
    # 検査の先に退院の日が続かないなら、選ぶと候補日が1つも残らない（「退院日は未定」。Plan main）。
    for option in options:
        if option.get("kind") == "検査" and len(option["days"]) == 1:
            option["note"] = _UNDECIDED
    return options if len(options) >= 2 else []


def next_rows(family: Family, branch: Branch, today: date) -> list[dict[str, Any]]:
    """分かれ道の先の日。医師の言葉の引用と、書かれた日と、決まった種類だけ。

    引用のもとの項目から日を読む。next が無い分かれ道は何も出さない（文字列から拾い直さない）。
    """
    by_id = {item.id: item for item in family.items}
    rows: list[dict[str, Any]] = []
    for outcome in branch.outcomes:
        source = by_id.get(outcome.clause_item_id or "")
        if source is None:
            continue
        base = _recorded_day(source, today)
        original = unmask_aliases(source.text_original or "", family.aliases)
        days_of = dict(clause_days(original, base))
        for step in outcome.next:
            clause = unmask_aliases(step.clause, family.aliases)
            day_word = unmask_aliases(step.day, family.aliases)
            days = days_of.get(clause) or _same_words_day(day_word, original, base)
            state = next((entry for entry in branch.candidate_days if entry.day == step.day), None)
            row = {
                "clause": clause,
                "day": _day_text(days[-1 if len(days) > 1 else 0]) if days else day_word,
                "kind": str(step.kind.value),
                "certainty": str(state.certainty.value) if state else "見込み",
                "cancelled": bool(state and state.cancelled),
            }
            if row not in rows:
                rows.append(row)
    return rows


def doctor_view(family: Family, today: date) -> dict[str, Any] | None:
    """「いま」の予定の欄。医師の言葉の節と、そこに書かれた日だけ。AIを呼ばず、台帳に書かない。

    案の前は札を付けない（どの節が退院の日かは、コードでは決められない）。
    案の後は、案の候補日だけを札つきで渡す。
    """
    conditions = [
        item
        for item in family.items
        if item.part == Part.HOSPITAL
        and item.condition
        and item.text_original
        and item.medical != Medical.ASK
    ]
    if not conditions:
        return None
    item = conditions[-1]
    base = _recorded_day(item, today)
    text = unmask_aliases(item.text_original or "", family.aliases)
    pairs = clause_days(text, base)
    clauses = [{"text": clause, "days": [_day_text(day) for day in days]} for clause, days in pairs]
    results = [
        other
        for other in family.items
        if other.refers_to == item.id and other.medical != Medical.ASK
    ]
    related = {item.id, *(other.id for other in results)}
    branch = family.branches[-1] if family.branches else None
    candidates: list[dict[str, Any]] = []
    paths: list[dict[str, Any]] = []
    if branch is not None and branch.condition_item_id in related:
        paths = next_rows(family, branch, today)
        candidates = candidate_rows(family, branch, today)
    first_day = min((day for _, days in pairs for day in days), default=None)
    return {
        "item_id": item.id,
        "recorded_at": item.recorded_at,
        "text": text,
        "clauses": clauses,
        "options": result_options(text, base),
        "after_title": after_title(text),
        # 結果がまだ入っていない間は、結果を入れる入口を出す。
        "open": not results,
        "due": first_day is not None and first_day <= today,
        "candidates": candidates,
        "paths": paths,
    }
