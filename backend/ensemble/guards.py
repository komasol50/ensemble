"""コードの守り。Claudeの出力をそのまま画面に出さないための関数。

どの関数もAIを呼ばない。違反の理由を文字列で返し、呼び出し側は
エラー付きで1回だけ呼び直す。2回目も落ちたら CANNOT_PROPOSE_TEXT を出す。
"""

import re
import unicodedata
from typing import Any

from .ledger import (
    Action,
    ActionType,
    Branch,
    Certainty,
    Family,
    Item,
    Kind,
    Medical,
    NextKind,
    Outcome,
    Part,
    Profile,
    Status,
)

MEDICAL_FIXED_TEXT = (
    "体調や治療のことには答えません。急ぐときは、今すぐナースコールを押してください。"
    "急がないことは、病棟のスタッフに直接確認してください。"
)
CANNOT_PROPOSE_TEXT = "案を出せませんでした。入れた文は残っています"
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


# 親が伝える症状の訴え。問いの形でなくても固定文を返す（緊急度は判定しない）。
# 「採血が」を拾わないよう、血は前の字を見る。
SYMPTOM_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"息が(苦|荒|し)",
        r"呼吸",
        r"ゼーゼー|ゼイゼイ|ヒューヒュー",
        r"けいれん|痙攣|ひきつけ",
        r"ぐったり",
        r"反応が(悪|な|鈍)",
        r"意識",
        r"顔色|真っ青|青白|唇が紫|チアノーゼ",
        r"吐い|吐き|嘔吐",
        r"(?<!採)血が|出血|血便|血を吐",
        r"腫れ|むくみ",
        r"痛い|痛み|痛が",
        r"ふるえ|震え",
        r"熱が|高熱|\d{2}度",
        r"起きない|目を覚まさない|呼びかけても",
        r"おしっこが.*出|尿が.*出",
        r"(水分|ミルク|母乳|水).*(飲まない|飲めない|とれない)",
        r"咳が|せきが",
        r"下痢|発疹|湿疹|じんましん",
    )
)


def find_symptom_words(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", text)
    return [m.group(0) for pattern in SYMPTOM_PATTERNS if (m := pattern.search(normalized))]


# 医療の語を書かない宛先。医師の説明にある語でも書かない。
NO_MEDICAL_WORD_PARTS = (Part.WORK, Part.CHILDCARE, Part.SUPPORT)
MEDICAL_WORD_ERROR = "医療の語"


def check_medical_words(
    draft: str | None, basis: list[Item], part: Part | None = None
) -> list[str]:
    """下書きの医療の語は、根拠にある医師の説明の原文に、その語があるときだけ通す。

    根拠に医師の説明が1つあるだけで通すと、医師の言葉はほぼ全部の案の根拠になるので、
    守りが効かなくなる。職場・保育園・支援への文には、説明にある語でも書かない。
    """
    if not draft:
        return []
    hits = find_medical_words(draft)
    if not hits:
        return []
    if part in NO_MEDICAL_WORD_PARTS:
        return [f"{part.value}への文に{MEDICAL_WORD_ERROR}があります: {hits}"]
    recorded = "".join(
        unicodedata.normalize("NFKC", item.text_original).lower()
        for item in basis
        if item.medical == Medical.INFO and item.text_original
    )
    unexplained = [word for word in hits if word not in recorded]
    if unexplained:
        return [f"下書きに{MEDICAL_WORD_ERROR}があり、医師の説明の原文にありません: {unexplained}"]
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


# ---- 使わない言葉 ----

# 下書きと仕分けの用件は、そのまま画面に出る。
BANNED_WORDS = (
    "交代",
    "代わる",
    "リレー",
    "バトン",
    "申し送り",
    "シフト",
    "分け合う",
    "チーム",
    "家族みんなで",
    "寄り添う",
    "抱え込ませない",
    "予測",
    "AIが判断",
    "相談できる",
    "不安を取り除く",
    "段取り",
    "不可",
    "見通し",
)


def check_words(text: str | None) -> list[str]:
    """画面に出る文に、使わない言葉が入っていれば止める。"""
    if not text:
        return []
    hits = [word for word in BANNED_WORDS if word in text]
    return [f"使わない言葉があります: {hits}"] if hits else []


# ---- 原文にない先の見込み ----

# 入院期間を推定しない。「さらに延びる」「長引く」は、根拠の原文にあるときだけ書ける。
# 候補日の範囲の「火曜まで延びそう」と、「まだ決まっていません」は止めない。
ADDED_OUTLOOK = re.compile(
    r"(?:さらに|もっと|これ以上|もう少し)[^。\n]{0,6}"
    r"(?:延び|伸び|のび|長引|長び|ながび|遅く|先に|延長(?!保育))"
    r"|長引|長び[くきい]|ながび"
    r"|(?:入院|退院|休み)[^。\n]{0,8}長くなり"
    r"|(?:もう|あと)数日"
)


def check_added_outlook(text: str | None, basis: list[Item]) -> list[str]:
    """根拠の原文にない、入院が延びる見込みを足していれば止める。"""
    hits = ADDED_OUTLOOK.findall(text or "")
    if not hits or any(ADDED_OUTLOOK.search(item.text_original or "") for item in basis):
        return []
    return [f"根拠の原文に無い先の見込みがあります: {hits}"]


# ---- 伝え直しの案 ----

FINISHED_STATUSES = (Status.DELIVERED, Status.DONE, Status.ANSWERED)


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


def check_release_is_open(action: Action, items: list[Item]) -> list[str]:
    """もう終わった項目は取り消せない。外すのは、まだ動いている項目だけ。"""
    by_id = {item.id: item for item in items}
    return [
        f"もう終わった項目は外せません: {item_id}"
        for item_id in action.release
        if item_id in by_id and by_id[item_id].status in FINISHED_STATUSES
    ]


def check_no_family_name(action: Action, profile: Profile) -> list[str]:
    """家庭の呼び名（家庭Aなど）は画面の中の呼び方。相手へ送る文に書かない。"""
    if action.draft and profile.name and profile.name in action.draft:
        return [f"下書きに家庭の呼び名「{profile.name}」が入っています"]
    return []


def check_action(
    action: Action, items: list[Item], profile: Profile, *, parent_edited: bool = False
) -> list[str]:
    """1つの案にすべての守りを当て、違反の理由を返す（空なら合格）。

    parent_edited は親が［進める］を押した後の確かめ。親は文を直せるので、
    日付と言葉の守り（AIの文のためのもの）は当てず、医療の語だけ当てる。
    """
    basis, violations = resolve_basis(action, items)
    violations += check_no_medical_ask_basis(basis)
    violations += check_action_type_for_certainty(action, basis)
    violations += check_family_request(action, profile)
    violations += check_release_is_open(action, items)
    violations += check_medical_words(action.draft, basis, action.part)
    if not parent_edited:
        violations += check_dates(action.draft, basis)
        violations += check_words(action.draft)
        violations += check_added_outlook(action.draft, basis)
        violations += check_no_family_name(action, profile)
    return violations


def condition_sources(branch: Branch, family: Family) -> list[Item]:
    """分かれ道のもとになった言葉。条件の項目と、それが指す項目、それを指す項目。"""
    by_id = {item.id: item for item in family.items}
    root = by_id.get(branch.condition_item_id)
    if root is None:
        return []
    ids = {root.id}
    if root.refers_to:
        ids.add(root.refers_to)
    ids |= {item.id for item in family.items if item.refers_to in ids}
    return [item for item in family.items if item.id in ids]


def check_branch(branch: Branch, family: Family, *, parent_edited: bool = False) -> list[str]:
    """分かれ道の全部に守りを当てる。候補日と見出しの日付は、条件のもとの言葉にあるものだけ。"""
    violations: list[str] = []
    if branch.condition_item_id not in {item.id for item in family.items}:
        violations.append(f"条件の項目IDが台帳にありません: {branch.condition_item_id}")
    sources = condition_sources(branch, family)
    for day in branch.candidate_days:
        violations += [f"候補日「{day.day}」: {v}" for v in check_dates(day.day, sources)]
    violations += check_test_days(branch)
    violations += check_restored_days(branch, family)
    for outcome in branch.outcomes:
        label = outcome.label
        violations += [f"見出し「{label}」: {v}" for v in check_words(label)]
        violations += [f"見出し「{label}」: {v}" for v in check_dates(label, sources)]
        violations += [f"見出し「{label}」: {v}" for v in check_medical_words(label, sources)]
        violations += [f"見出し「{label}」: {v}" for v in check_added_outlook(label, sources)]
        for action in outcome.actions:
            violations += [
                f"{action.part}（{action.type}）: {v}"
                for v in check_action(
                    action, family.items, family.profile, parent_edited=parent_edited
                )
            ]
    return violations


def _squeezed(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


def check_test_days(branch: Branch) -> list[str]:
    """検査の日は退院の候補日にしない。"""
    test_days: set[str] = set()
    for outcome in branch.outcomes:
        for step in outcome.next:
            if step.kind == NextKind.TEST:
                test_days |= extract_date_tokens(step.day)
    # 「月曜」と「月曜日」を同じ日として比べる。
    return [
        f"候補日「{day.day}」: 検査の日は候補日に入れません"
        for day in branch.candidate_days
        if not day.cancelled and extract_date_tokens(day.day) & test_days
    ]


def check_restored_days(branch: Branch, family: Family) -> list[str]:
    """直前の案で取り消した日は、新しい条件の項目の原文にその日が無い限り戻せない。

    前の案を渡すようになったので、親が取り消した日をAIが候補に戻すのを止める。
    """
    if not family.branches:
        return []
    previous = family.branches[-1]
    by_id = {item.id: item for item in family.items}
    condition = by_id.get(branch.condition_item_id)
    is_new_condition = branch.condition_item_id != previous.condition_item_id
    words = extract_date_tokens((condition.text_original or "") if condition else "")
    found = []
    for day in branch.candidate_days:
        if day.cancelled:
            continue
        tokens = extract_date_tokens(day.day)
        was_cancelled = any(
            old.cancelled and extract_date_tokens(old.day) == tokens
            for old in previous.candidate_days
        )
        if was_cancelled and not (is_new_condition and extract_date_tokens(day.day) <= words):
            found.append(f"候補日「{day.day}」: 取り消した日は戻せません")
    return found


def _quote_of_hospital(outcome: Outcome, by_id: dict[str, Item]) -> str | None:
    """引用の出どころの原文。病院の項目だけ。親の入れた文や親が直した文は医師の言葉にしない。"""
    source = by_id.get(outcome.clause_item_id or "")
    if source is None or source.part != Part.HOSPITAL or not source.text_original:
        return None
    if source.speaker in PARENT_SPEAKERS:
        return None
    return _squeezed(source.text_original)


MIN_QUOTE_CHARS = 5


def keep_true_quotes(branch: Branch, family: Family) -> Branch:
    """引用の欄を確かめる。確かめられないものは捨てる（画面は今の表示へ戻る）。

    次の日は分かれ道ごとに全か無か。1つでも落ちたら全部捨て、片方だけを候補のように描かない。
    日は同じ節の原文に書かれた曜日か日付だけを通す。前の案や今日の日付は根拠にしない。
    """
    by_id = {item.id: item for item in family.items}
    cleaned = branch.model_copy(deep=True)
    for outcome in cleaned.outcomes:
        original = _quote_of_hospital(outcome, by_id)

        def quoted(words: str | None) -> bool:
            # 「月曜」だけの短い引用は、節の引用として認めない。
            if not words or original is None or len(_squeezed(words)) < MIN_QUOTE_CHARS:
                return False
            return _squeezed(words) in original

        if not quoted(outcome.clause):
            outcome.clause = None
            outcome.clause_item_id = None
        steps_true = all(
            quoted(step.clause)
            and extract_date_tokens(step.day)
            and extract_date_tokens(step.day) <= extract_date_tokens(step.clause)
            for step in outcome.next
        )
        if not steps_true:
            outcome.next = []
        for action in outcome.actions:
            if action.ask and _squeezed(action.ask) not in _squeezed(action.draft or ""):
                action.ask = None
    return cleaned


def check_condition_stage(branch: Branch) -> list[str]:
    """結果が出る前の案は、確認だけ。

    「確定の根拠が1つあれば頼める」は結果が入った後のための読み方で、
    無関係な確定の項目を根拠に混ぜれば、ここを通れてしまうので別に止める。
    """
    allowed = (ActionType.CONFIRM, ActionType.CHECK_AVAILABILITY, ActionType.NOTHING)
    return [
        f"{action.part}（{action.type}）: 結果が出る前は確認だけです"
        for outcome in branch.outcomes
        for action in outcome.actions
        if action.type not in allowed
    ]


def check_result_stage(branch: Branch, trigger: Item | None) -> list[str]:
    """結果の後でも、その結果が確定でなければ「頼む」「伝える」は出さない。

    根拠に無関係な確定の項目が1つあるだけでは、頼む理由にならないため。
    """
    empty = [
        f"「{outcome.label}」: 結果の先の案がありません。宛先ごとの案を actions に入れてください"
        for outcome in branch.outcomes
        if not outcome.actions
    ]
    if trigger is not None and trigger.certainty == Certainty.CONFIRMED:
        return empty
    return empty + [
        f"{action.part}（{action.type}）: 結果が確定していないので確認だけです"
        for outcome in branch.outcomes
        for action in outcome.actions
        if action.type in (ActionType.REQUEST, ActionType.TELL)
    ]


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


def is_own_note(item: Item) -> bool:
    """親自身の一言（食事・睡眠・体調など）。連絡の材料にしないので、AIへ渡さない。"""
    return item.part == Part.PARENT and item.kind == Kind.INFO


def mark_symptom_as_ask(item: Item) -> Item:
    """親と家族の言葉の症状の訴えは、AIの仕分けによらず固定文にする。

    医師や保育園の言葉は記録なので当てない。誤って固定文を出しても害は小さいので、広めに取る。
    """
    if item.medical == Medical.ASK:
        return item
    if (
        item.speaker is not None
        and item.speaker not in PARENT_SPEAKERS
        and item.part != Part.FAMILY
    ):
        return item
    text = item.text_original or item.task_summary or ""
    if find_symptom_words(text):
        return item.model_copy(update={"medical": Medical.ASK})
    return item


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
