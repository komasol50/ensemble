"""コードの守り。Claudeの出力をそのまま画面に出さないための関数。

どの関数もAIを呼ばない。違反の理由を文字列で返し、呼び出し側は
エラー付きで1回だけ呼び直す。2回目も落ちたら CANNOT_PROPOSE_TEXT を出す。
"""

import re
import unicodedata
from typing import Any

from .ledger import Action, ActionType, Branch, Certainty, Family, Item, Medical, Part, Profile

MEDICAL_FIXED_TEXT = (
    "体調や治療のことには答えません。急ぐときは、今すぐナースコールを押してください。"
    "急がないことは、病棟のスタッフに直接確認してください。"
)
CANNOT_PROPOSE_TEXT = "案を出せませんでした。原文はボードにあります"
THIRD_PARTY_NOTICE = "用件だけ残し、原文は保存していません"
INPUT_MAX_CHARS = 2000


# ---- 医療の線 ----


def medical_reply(item: Item) -> str | None:
    """判断を求める問いには、内容によらず同じ固定文を返す。それ以外には返さない。"""
    return MEDICAL_FIXED_TEXT if item.medical == Medical.ASK else None


MEDICAL_WORDS = (
    "薬",
    "量",
    "mg",
    "ミリグラム",
    "投与",
    "中止",
    "増やす",
    "減らす",
    "服用",
    "点滴",
    "副作用",
    "解熱",
)
MEDICAL_PATTERNS = (re.compile(r"熱が(何|\d+)度"), re.compile(r"\d+(\.\d+)?度(以上|以下|を超)"))
# 生活の用事として出てくる語。医療の判断ではないので語のチェックから外す。
LIFE_PHRASES = ("お薬手帳", "薬局", "荷物の量", "洗濯物の量")


def find_medical_words(draft: str) -> list[str]:
    text = unicodedata.normalize("NFKC", draft).lower()
    for phrase in LIFE_PHRASES:
        text = text.replace(phrase, "")
    hits = [word for word in MEDICAL_WORDS if word in text]
    hits += [m.group(0) for pattern in MEDICAL_PATTERNS if (m := pattern.search(text))]
    return hits


def check_medical_words(draft: str | None, basis: list[Item]) -> list[str]:
    """下書きが医療の語に当たり、根拠に医師の説明の記録が無ければ止める。"""
    if not draft:
        return []
    hits = find_medical_words(draft)
    if hits and not any(item.medical == Medical.INFO for item in basis):
        return [f"下書きに医療の語があり、根拠に医師の説明がありません: {hits}"]
    return []


# ---- 日付の守り ----

_WEEKDAYS = "月火水木金土日"
_RELATIVE_DAYS = (
    "明後日",
    "あさって",
    "今日",
    "明日",
    "あした",
    "昨日",
    "今夜",
    "今晩",
    "今朝",
    "今週",
    "来週",
    "再来週",
    "週末",
)
_MONTH_DAY = re.compile(r"(\d{1,2})月(\d{1,2})日|(?<![\d/])(\d{1,2})/(\d{1,2})(?![\d/])")
_DAY_ONLY = re.compile(r"(?<![\d月/])(\d{1,2})日(?!分|間|目)")
_CLOCK = re.compile(r"(\d{1,2}):(\d{2})")
_HOUR_MIN = re.compile(r"(\d{1,2})時(?:(\d{1,2})分|(半))?(?!間)")
_WEEKDAY_FULL = re.compile(rf"([{_WEEKDAYS}])曜日?")
_WEEKDAY_PAREN = re.compile(rf"[(]([{_WEEKDAYS}])(?:曜日?)?[)]")
# 「月・火」「木、金」のように並べた曜日と、「木の延長」のように曜日だけで書いたもの。
_WEEKDAY_LIST = re.compile(rf"(?<![一-龥\d])([{_WEEKDAYS}])(?=[・、][{_WEEKDAYS}])")
_WEEKDAY_LIST_TAIL = re.compile(rf"(?<=[・、])([{_WEEKDAYS}])(?![一-龥])")
_WEEKDAY_BARE_NO = re.compile(r"(?<![一-龥\dお])([月火水木金土])(?=の)")


def extract_date_tokens(text: str) -> set[str]:
    """文に出てくる日付・曜日・時刻を、比べられる形にそろえて取り出す。"""
    text = unicodedata.normalize("NFKC", text)
    tokens: set[str] = set()

    def take(pattern: re.Pattern[str], build: Any) -> None:
        nonlocal text
        for match in pattern.finditer(text):
            tokens.add(build(match))
        text = pattern.sub(" ", text)

    take(_MONTH_DAY, lambda m: f"{int(m[1] or m[3])}/{int(m[2] or m[4])}")
    take(_DAY_ONLY, lambda m: f"{int(m[1])}日")
    take(_CLOCK, lambda m: f"{int(m[1])}:{m[2]}")
    take(
        _HOUR_MIN,
        lambda m: f"{int(m[1])}:{'30' if m[3] else f'{int(m[2] or 0):02d}'}",
    )
    take(_WEEKDAY_FULL, lambda m: f"{m[1]}曜")
    take(_WEEKDAY_PAREN, lambda m: f"{m[1]}曜")
    take(_WEEKDAY_LIST, lambda m: f"{m[1]}曜")
    take(_WEEKDAY_LIST_TAIL, lambda m: f"{m[1]}曜")
    take(_WEEKDAY_BARE_NO, lambda m: f"{m[1]}曜")
    # 長い語から先に消す（「再来週」の中の「来週」を拾わない）。
    for word in sorted(_RELATIVE_DAYS, key=len, reverse=True):
        if word in text:
            tokens.add(word)
            text = text.replace(word, " ")
    return tokens


def check_dates(draft: str | None, basis: list[Item]) -> list[str]:
    """下書きの日付・曜日・時刻が根拠の原文に無ければ止める。"""
    if not draft:
        return []
    allowed: set[str] = set()
    for item in basis:
        if item.text_original:
            allowed |= extract_date_tokens(item.text_original)
    invented = extract_date_tokens(draft) - allowed
    if invented:
        return [f"根拠の原文に無い日付・時刻があります: {sorted(invented)}"]
    return []


# ---- 伝え直しの案 ----


def resolve_basis(action: Action, items: list[Item]) -> tuple[list[Item], list[str]]:
    """根拠IDを台帳の項目に引き当てる。無いIDは違反として返す。"""
    by_id = {item.id: item for item in items}
    violations = [
        f"根拠IDが台帳にありません: {basis_id}"
        for basis_id in action.basis_ids
        if basis_id not in by_id
    ]
    if action.type != ActionType.NOTHING and not action.basis_ids:
        violations.append("根拠IDがありません")
    violations += [
        f"外す項目のIDが台帳にありません: {item_id}"
        for item_id in action.release
        if item_id not in by_id
    ]
    return [by_id[b] for b in action.basis_ids if b in by_id], violations


def check_action_type_for_certainty(action: Action, basis: list[Item]) -> list[str]:
    """根拠がすべて見込み以下なら、案は確認に限る。

    親が入れた確定の事実が1つでも根拠にあれば、頼む・伝えるを出してよい
    （例：医師の「かな」＋母の「今日の採血はだめでした」）。
    """
    if not basis or action.type in (
        ActionType.CONFIRM,
        ActionType.CHECK_AVAILABILITY,
        ActionType.NOTHING,
    ):
        return []
    if all(item.certainty != Certainty.CONFIRMED for item in basis):
        return [f"根拠が見込みだけなので「{action.type}」は出せません"]
    return []


def check_family_request(action: Action, profile: Profile) -> list[str]:
    """用事を頼める人がいない家庭では、家族への依頼を出さない。"""
    if (
        action.part == Part.FAMILY
        and not profile.errand_people
        and action.type in (ActionType.REQUEST, ActionType.CONFIRM, ActionType.CHECK_AVAILABILITY)
    ):
        return ["用事を頼める人がいない家庭に、家族への依頼は出せません"]
    return []


def check_no_medical_ask_basis(basis: list[Item]) -> list[str]:
    """判断を求める問いは、行動の案にしない。"""
    if any(item.medical == Medical.ASK for item in basis):
        return ["医療の問いを根拠にした案は出せません"]
    return []


def check_action(action: Action, items: list[Item], profile: Profile) -> list[str]:
    """1つの案にすべての守りを当て、違反の理由を返す（空なら合格）。"""
    basis, violations = resolve_basis(action, items)
    violations += check_no_medical_ask_basis(basis)
    violations += check_action_type_for_certainty(action, basis)
    violations += check_family_request(action, profile)
    violations += check_dates(action.draft, basis)
    violations += check_medical_words(action.draft, basis)
    return violations


def check_branch(branch: Branch, family: Family) -> list[str]:
    """分かれ道の全部に守りを当てる。候補日も、台帳の原文にある日だけ。"""
    violations: list[str] = []
    if branch.condition_item_id not in {item.id for item in family.items}:
        violations.append(f"条件の項目IDが台帳にありません: {branch.condition_item_id}")
    for day in branch.candidate_days:
        violations += [f"候補日「{day.day}」: {v}" for v in check_dates(day.day, family.items)]
    for outcome in branch.outcomes:
        for action in outcome.actions:
            violations += [
                f"{action.part}（{action.type}）: {v}"
                for v in check_action(action, family.items, family.profile)
            ]
    return violations


# ---- 取り込み ----

CONDITION_WORDS = ("なら", "かも", "かな", "予定", "見込み", "しだい", "次第")


def has_date(text: str) -> bool:
    return bool(extract_date_tokens(text))


def lower_certainty(item: Item) -> Item:
    """確定と言えない項目の確かさを「見込み」に下げる。上げることはしない。"""
    if item.certainty != Certainty.CONFIRMED:
        return item
    text = item.text_original or item.task_summary or ""
    if any(word in text for word in CONDITION_WORDS) or not has_date(text):
        return item.model_copy(update={"certainty": Certainty.LIKELY})
    return item


REQUIRED_ITEM_FIELDS = ("kind", "part", "certainty")


def is_savable(raw: dict[str, Any]) -> bool:
    """種類・相手・確かさが空の項目は保存しない。"""
    return all(raw.get(field) not in (None, "") for field in REQUIRED_ITEM_FIELDS)


_THIRD_PARTY_PARTS = (Part.WORK, Part.CHILDCARE, Part.SUPPORT)
PARENT_SPEAKERS = ("親", "母", "父", "私", "自分")


def is_third_party(item: Item) -> bool:
    """職場・保育園・支援の人が書いた文か。発言者が不明なら第三者として扱う。"""
    if item.part not in _THIRD_PARTY_PARTS:
        return False
    return item.speaker not in PARENT_SPEAKERS


def strip_third_party(item: Item, pasted_at: str) -> Item:
    """第三者の文は原文を保存せず、用件の要約と貼った時刻だけ残す。

    要約が無いと用件が消えるので、その場合は例外にして保存させない。
    """
    if not is_third_party(item):
        return item
    if not item.task_summary:
        raise ValueError("第三者の文に用件の要約がありません")
    return item.model_copy(update={"text_original": None, "recorded_at": pasted_at})


# ---- 呼び名 ----


def mask_aliases(text: str, aliases: dict[str, str]) -> str:
    """登録した呼び名を、送る前に［子］［上司］へ置き換える。長い呼び名から先に。"""
    for name in sorted(aliases, key=len, reverse=True):
        text = text.replace(name, aliases[name])
    return text


def unmask_aliases(text: str, aliases: dict[str, str]) -> str:
    """表示のときに呼び名へ戻す。"""
    for name, placeholder in aliases.items():
        text = text.replace(placeholder, name)
    return text


def check_input_length(text: str) -> bool:
    return len(text) <= INPUT_MAX_CHARS
