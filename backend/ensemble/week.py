"""週の表。台帳の文に書かれた曜日と日付から描く。AIを呼ばず、予定を作らない。

マスの中は原文のまま。言い換えると、誰も言っていないことが表に出るため。
曜日は、その文が記録された日から見た次の曜日として置く。
"""

import re
from datetime import date, datetime, timedelta
from typing import Any

from .guards import extract_date_tokens, unmask_aliases
from .ledger import Branch, Certainty, Family, Item, Medical, Part, Status

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


def split_clauses(text: str) -> list[str]:
    return [clause.strip() for clause in _CLAUSE.split(_SPEAKER.sub("", text)) if clause.strip()]


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


def week_view(family: Family, today: date, branch: Branch | None = None) -> dict[str, Any] | None:
    """画面へ渡す週の表。描くものが無ければ None。

    branch は親が押す前の案。無ければ台帳の最新の案を使う。
    """
    latest = branch or (family.branches[-1] if family.branches else None)
    by_id = {item.id: item for item in family.items}
    rows = [today + timedelta(days=offset) for offset in range(DAYS_AFTER_TODAY + 1)]
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

    if latest is not None:
        base = _recorded_day(by_id.get(latest.condition_item_id), today)
        for candidate in latest.candidate_days:
            cell = _candidate_cell(candidate.certainty, candidate.cancelled)
            days = dates_in(candidate.day, base)
            if not days:
                # 曜日にならない候補日（「来週前半」など）も、隠さずに表の外へ出す。
                others.append(f"{candidate.day}（{cell['text']}）")
            for day in days:
                put(day, 0, cell)
                has_candidate.add(day)

    released = _released_ids(latest)
    for item in family.items:
        column = PART_COLUMN.get(item.part)
        if column is None or item.status in FINISHED or item.medical == Medical.ASK:
            continue
        base = _recorded_day(item, today)
        where = {"item_id": item.id, "part": str(item.part.value)}
        style = "released" if item.id in released else "plain"
        text = item.text_original or item.task_summary or ""
        for clause in split_clauses(text):
            for day in dates_in(clause, base):
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
            }
            for day in rows
        ],
        "others": [unmask_aliases(text, family.aliases) for text in others],
    }
