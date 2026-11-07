"""画面の検査のためのサーバー。Claudeの代わりに決まった応答を返す。外へは何も送らない。

.env に本物のキーがあっても使わない。入れた文で応答を決める。
使い方: python -m uvicorn fake_ai_app:app --app-dir scripts
"""

import json
import re
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import ensemble.intake  # noqa: E402
import ensemble.reading  # noqa: E402
import ensemble.retell  # noqa: E402
from ensemble.intake import SortedItem, SortedItems  # noqa: E402
from ensemble.ledger import (  # noqa: E402
    ActionType,
    Branch,
    Certainty,
    Kind,
    Medical,
    NextKind,
    Part,
)
from ensemble.main import create_app  # noqa: E402
from ensemble.reading import (  # noqa: E402
    FoundQuote,
    LedgerSearchOutput,
    ReadOutput,
    SearchOutput,
)

DELAY_SECONDS = 1.0  # 「仕分け中」を見届けるための待ち

# 偽の応答を本物の呼び出しの記録に混ぜない。
ensemble.intake.record_ai_call = lambda *a, **k: None
ensemble.retell.record_ai_call = lambda *a, **k: None
ensemble.reading.record_ai_call = lambda *a, **k: None

SLIPPERS = "スリッパはお控えください。"
# 図の④の答え（受け渡しの候補の語）。しおりの文そのまま。
HANDOFF = "荷物の受け渡し時間は、9:00〜20:00です。"


def sorted_item(text: str, **overrides: Any) -> SortedItem:
    values: dict[str, Any] = {
        "kind": Kind.INFO,
        "part": Part.HOSPITAL,
        "certainty": Certainty.LIKELY,
        "text_original": text,
        "task_summary": None,
        "speaker": "親",
        "condition": None,
        "medical": Medical.NONE,
        "refers_to": None,
    }
    return SortedItem(**{**values, **overrides})


def fake_intake(content: str) -> SortedItems | None:
    text = re.search(r"<pasted>\n(.*)\n</pasted>", content, re.S)[1]
    ledger = json.loads(re.search(r"<ledger>\n(.*?)\n</ledger>", content, re.S)[1])
    doctor = next(
        (item for item in ledger["items"] if item.get("condition") and item.get("part") == "病院"),
        None,
    )
    if "失敗" in text:
        return None
    if "退院できるなら" in text:
        return figure_intake(text)
    if "しだい" in text:
        return SortedItems(
            items=[sorted_item(text, condition="採血の結果", medical=Medical.INFO, speaker="医師")]
        )
    if "だめでした" in text or "と言われた" in text:
        return SortedItems(
            items=[
                sorted_item(
                    text,
                    certainty=Certainty.CONFIRMED,
                    medical=Medical.INFO,
                    refers_to=doctor["id"] if doctor else None,
                )
            ]
        )
    if "薬" in text:
        return SortedItems(
            items=[sorted_item(text, kind=Kind.QUESTION, task_summary="質問", medical=Medical.ASK)]
        )
    if text.startswith("上司"):
        return SortedItems(
            items=[
                sorted_item(
                    text,
                    kind=Kind.ACTION,
                    part=Part.WORK,
                    task_summary="会議の連絡",
                    speaker="上司",
                )
            ]
        )
    # 判断を求める問い（偽のAIの決まり。本物は指示文で仕分ける）。eval の台本の医療の問いに合わせる。
    if any(phrase in text for phrase in ("大丈夫なの", "平気", "の意味は")):
        return SortedItems(
            items=[sorted_item(text, kind=Kind.QUESTION, task_summary="質問", medical=Medical.ASK)]
        )
    is_question = text.rstrip().endswith(("？", "?")) or "っけ" in text
    # 夫のことを聞く問いは家族への質問（④の「質問する」で、病院のことかどうかを分けるため）。
    if is_question and "夫" in text:
        return SortedItems(items=[sorted_item(text, kind=Kind.QUESTION, part=Part.FAMILY)])
    # 問いの形の文は、病院への質問にする（②に選ぶ札が無いので、文から仕分ける）。
    if is_question:
        return SortedItems(items=[sorted_item(text, kind=Kind.QUESTION)])
    if "売店" in text:
        return SortedItems(items=[sorted_item(text, kind=Kind.ACTION, part=Part.PARENT)])
    return SortedItems(items=[sorted_item(text)])


# 図（新UI_2026-11-05）の筋書き。12日に採血、退院できるなら16日、再検査なら17日。
FIGURE_DOCTOR = "先生から「12日に採血。退院できるなら16日が候補、再検査なら17日」と言われた。"
FIGURE_REQUEST = "着替えも頼みたい。場所は寝室の棚。"


def figure_intake(text: str) -> SortedItems:
    """医師の言葉と、夫への頼みごとに分ける。原文は貼った文からそのまま。"""
    items = [
        sorted_item(
            FIGURE_DOCTOR if FIGURE_DOCTOR in text else text,
            condition="採血の結果",
            medical=Medical.INFO,
            speaker="医師",
            task_summary="採血の結果で退院の候補が変わる",
        )
    ]
    if FIGURE_REQUEST in text:
        items.append(
            sorted_item(
                FIGURE_REQUEST,
                kind=Kind.ACTION,
                part=Part.FAMILY,
                task_summary="着替え",
                certainty=Certainty.CONFIRMED,
            )
        )
    return SortedItems(items=items)


def figure_result_b(doctor_id: str, basis: Any, release_of: Any) -> Branch:
    """家庭B（夫に平日の迎えを頼めない）の再検査。16日に頼んだ延長保育とファミサポの迎えを取り消す。"""
    clause = "再検査なら17日"
    return Branch.model_validate(
        {
            "id": "branch-1",
            "condition_item_id": doctor_id,
            "candidate_days": [{"day": "16日", "certainty": "見込み", "cancelled": True}],
            "outcomes": [
                {
                    "label": "再検査になった",
                    "clause": clause,
                    "clause_item_id": doctor_id,
                    "next": [{"day": "17日", "kind": NextKind.TEST, "clause": clause}],
                    "actions": [
                        {
                            "part": Part.CHILDCARE,
                            "type": ActionType.TELL,
                            "draft": (
                                "お世話になっております。16日にお願いしていた延長保育は、"
                                "退院日が決まらなくなったため、取り消させてください。\n"
                                "日が決まりしだい、あらためてお知らせします。"
                            ),
                            "basis_ids": basis("保育園・預け先"),
                            "release": release_of("保育園・預け先"),
                            "ask": "日が決まりしだい、あらためてお知らせします。",
                        },
                        {
                            "part": Part.SUPPORT,
                            "type": ActionType.TELL,
                            "draft": (
                                "お世話になっております。16日の18:30からのお迎えは、"
                                "退院日が決まらなくなったため、いったん取り消させてください。\n"
                                "日が決まりしだい、あらためてご相談させてください。"
                            ),
                            "basis_ids": basis("支援"),
                            "release": release_of("支援"),
                            "ask": "日が決まりしだい、あらためてご相談させてください。",
                        },
                        {
                            "part": Part.WORK,
                            "type": ActionType.TELL,
                            "draft": (
                                "お疲れさまです。17日に再検査になり、退院日はまだ決まっていません。\n"
                                "休みの候補日は、決まりしだいご連絡します。"
                            ),
                            "basis_ids": basis("職場"),
                            "ask": "休みの候補日は、決まりしだいご連絡します。",
                        },
                    ],
                }
            ],
        }
    )


def figure_condition(doctor_id: str, basis: Any) -> Branch:
    """図の文の条件の段。16日は退院の候補（見込み）、17日は再検査。確認だけ。"""
    return Branch.model_validate(
        {
            "id": "branch-1",
            "condition_item_id": doctor_id,
            "candidate_days": [{"day": "16日", "certainty": "見込み"}],
            "outcomes": [
                {
                    "label": "退院できる",
                    "clause": "退院できるなら16日が候補",
                    "clause_item_id": doctor_id,
                    "next": [
                        {
                            "day": "16日",
                            "kind": NextKind.DISCHARGE,
                            "clause": "退院できるなら16日が候補",
                        }
                    ],
                    "actions": [
                        {
                            "part": Part.CHILDCARE,
                            "type": ActionType.CHECK_AVAILABILITY,
                            "draft": "お世話になっております。16日の延長保育の空きを確認させてください。",
                            "basis_ids": basis("保育園・預け先"),
                            "ask": "16日の延長保育の空きを確認させてください。",
                        }
                    ],
                },
                {
                    "label": "再検査になる",
                    "clause": "再検査なら17日",
                    "clause_item_id": doctor_id,
                    "next": [{"day": "17日", "kind": NextKind.TEST, "clause": "再検査なら17日"}],
                    "actions": [],
                },
            ],
        }
    )


def figure_result(doctor_id: str, said: str, basis: Any, carry: list[str]) -> Branch:
    """採血のあと。再検査なら16日を取り消し、夫・職場・保育園に1通ずつ。退院なら確認だけ。"""
    if "再検査" in said:
        clause, label = "再検査なら17日", "再検査になった"
        steps = [{"day": "17日", "kind": NextKind.TEST, "clause": clause}]
        candidates = [{"day": "16日", "certainty": "見込み", "cancelled": True}]
        actions = [
            {
                "part": Part.FAMILY,
                "type": ActionType.REQUEST,
                "draft": (
                    "17日に再検査で、退院日は未定です。\n" "寝室の棚の着替えを持ってきてもらえる？"
                ),
                # 親の「着替えも頼みたい」を引き継ぐ（前の文は送る一覧から外れる）。
                "basis_ids": basis("家族") + carry,
                "ask": "寝室の棚の着替えを持ってきてもらえる？",
            },
            # 同じ相手の2つ目の用件。画面で1通にまとめるので挨拶を書かない（図の⑥の夫の2行）。
            {
                "part": Part.FAMILY,
                "type": ActionType.TELL,
                "draft": "16日の迎えは、いったん待ってね。",
                "basis_ids": basis("家族"),
                "ask": "16日の迎えは、いったん待ってね。",
            },
            {
                "part": Part.WORK,
                "type": ActionType.TELL,
                "draft": (
                    "お疲れさまです。17日に再検査になり、退院日はまだ決まっていません。\n"
                    "休みの候補日は、決まりしだいご連絡します。\n"
                    "扱いはご相談させてください。"
                ),
                "basis_ids": basis("職場"),
                "ask": "休みの候補日は、決まりしだいご連絡します。",
            },
            {
                "part": Part.CHILDCARE,
                "type": ActionType.TELL,
                "draft": (
                    "お世話になっております。16日にお休みとお伝えしていましたが、"
                    "退院日がまだ決まっていません。\n"
                    "上の子のお休みの日は、決まりしだいお知らせします。"
                ),
                "basis_ids": basis("保育園・預け先"),
                "ask": "上の子のお休みの日は、決まりしだいお知らせします。",
            },
        ]
    else:
        clause, label = "退院できるなら16日が候補", "退院の予定になった"
        steps = [{"day": "16日", "kind": NextKind.DISCHARGE, "clause": clause}]
        candidates = [{"day": "16日", "certainty": "見込み"}]
        actions = [
            {
                "part": Part.FAMILY,
                "type": ActionType.CONFIRM,
                "draft": "16日に退院できるかもって。まだ決まってはいないよ。\n16日の迎え、行けそう？",
                "basis_ids": basis("家族"),
                "ask": "16日の迎え、行けそう？",
            },
        ]
    return Branch.model_validate(
        {
            "id": "branch-1",
            "condition_item_id": doctor_id,
            "candidate_days": candidates,
            "outcomes": [
                {
                    "label": label,
                    "clause": clause,
                    "clause_item_id": doctor_id,
                    "next": steps,
                    "actions": actions,
                }
            ],
        }
    )


def good_result(doctor_id: str, has_story: bool, has_errand: bool, basis: Any) -> Branch:
    """「木曜に帰れそうと言われた」のあと。見込みなので、頼まずに確認する。"""
    actions = [
        {
            "part": Part.CHILDCARE,
            "type": ActionType.CHECK_AVAILABILITY,
            "draft": "お世話になっております。木曜の延長保育の空きを確認させてください。\nまだ確定ではありません。",
            "basis_ids": basis("保育園・預け先"),
            "ask": "木曜の延長保育の空きを確認させてください。",
        },
        {
            "part": Part.WORK,
            "type": ActionType.CONFIRM,
            "draft": "お疲れさまです。木曜に退院の見込みです。\nまだ確定ではありません。\n木曜の会議の扱いを確認させてください。",
            "basis_ids": basis("職場"),
            "ask": "木曜の会議の扱いを確認させてください。",
        },
    ]
    if has_errand:
        actions.insert(
            0,
            {
                "part": Part.FAMILY,
                "type": ActionType.CONFIRM,
                "draft": "木曜に帰れそうって。まだ決まってはいないよ。\n木曜の夜、家にいられそう？",
                "basis_ids": basis("家族"),
                "ask": "木曜の夜、家にいられそう？",
            },
        )
    return Branch.model_validate(
        {
            "id": "branch-1",
            "condition_item_id": doctor_id,
            "candidate_days": [{"day": "木曜", "certainty": "見込み"}] if has_story else [],
            "outcomes": [
                {
                    "label": "採血がよかった",
                    "clause": "よければ木曜に帰れるかも",
                    "clause_item_id": doctor_id,
                    "next": [
                        {
                            "day": "木曜",
                            "kind": NextKind.DISCHARGE,
                            "clause": "よければ木曜に帰れるかも",
                        }
                    ],
                    "actions": actions,
                }
            ],
        }
    )


def fake_retell(content: str) -> Branch:
    """本物のAIと同じ形で案を返す。作った家庭でも、台帳にある項目だけを根拠にする。"""
    trigger = re.search(r"trigger_item_id: (\S+)", content)[1]
    stage = re.search(r"stage: (\S+)", content)[1]
    ledger = json.loads(re.search(r"<ledger>\n(.*?)\n</ledger>", content, re.S)[1])
    items = ledger["items"]
    doctor = next(
        (item for item in items if item.get("condition") and item.get("part") == "病院"), None
    )
    doctor_id = doctor["id"] if doctor else trigger
    original = (doctor or {}).get("text_original") or ""
    has_story = "木曜" in original and "火曜" in original and "月曜" in original
    figure = "退院できるなら" in original
    has_errand = bool(ledger["profile"]["errand_people"])

    def first_of(part: str) -> str | None:
        return next((item["id"] for item in items if item.get("part") == part), None)

    def basis(part: str) -> list[str]:
        found = first_of(part)
        return [entry for entry in dict.fromkeys([found, doctor_id, trigger]) if entry]

    if figure and stage == "condition":
        return figure_condition(doctor_id, basis)
    if stage == "condition":
        return Branch.model_validate(
            {
                "id": "branch-1",
                "condition_item_id": doctor_id,
                "candidate_days": (
                    [
                        {"day": "木曜", "certainty": "見込み"},
                        {"day": "火曜", "certainty": "見込み"},
                    ]
                    if has_story
                    else []
                ),
                "outcomes": [
                    {
                        "label": "採血がよかった",
                        "clause": "よければ木曜に帰れるかも",
                        "clause_item_id": doctor_id,
                        "next": [
                            {
                                "day": "木曜",
                                "kind": NextKind.DISCHARGE,
                                "clause": "よければ木曜に帰れるかも",
                            }
                        ],
                        "actions": [
                            {
                                "part": Part.CHILDCARE,
                                "type": ActionType.CHECK_AVAILABILITY,
                                "draft": "お世話になっております。木曜の延長保育の空きを確認させてください。",
                                "basis_ids": basis("保育園・預け先"),
                                "ask": "木曜の延長保育の空きを確認させてください。",
                            }
                        ],
                    },
                    {
                        "label": "採血がだめだった",
                        "clause": "だめなら月曜にもう一回とって",
                        "clause_item_id": doctor_id,
                        "next": [
                            {
                                "day": "月曜",
                                "kind": NextKind.TEST,
                                "clause": "だめなら月曜にもう一回とって",
                            },
                            {
                                "day": "火曜",
                                "kind": NextKind.DISCHARGE,
                                "clause": "それで大丈夫なら火曜かな",
                            },
                        ],
                        "actions": [
                            {
                                "part": Part.CHILDCARE,
                                "type": ActionType.CHECK_AVAILABILITY,
                                "draft": "お世話になっております。火曜の延長保育の空きを確認させてください。",
                                "basis_ids": basis("保育園・預け先"),
                                "ask": "火曜の延長保育の空きを確認させてください。",
                            }
                        ],
                    },
                ],
            }
        )

    said = next((item.get("text_original") or "" for item in items if item["id"] == trigger), "")
    if figure and stage != "condition":
        carry = [
            item["id"]
            for item in items
            if item.get("part") == "家族" and item.get("status") == "頼みたい"
        ]
        supports = [support["name"] for support in ledger["profile"]["supports"]]
        if "再検査" in said and any("ファミサポ" in name for name in supports):

            def release_of(part: str) -> list[str]:
                return [
                    i["id"]
                    for i in items
                    if i.get("part") == part and "16日" in (i.get("text_original") or "")
                ]

            return figure_result_b(doctor_id, basis, release_of)
        return figure_result(doctor_id, said, basis, carry)
    if "帰れそう" in said:
        return good_result(doctor_id, has_story, has_errand, basis)

    release = [
        item["id"]
        for item in items
        if item.get("part") == "保育園・預け先" and "木" in (item.get("task_summary") or "")
    ]
    actions = [
        # 文は本物のAIの出力（evals/retell/results の家庭A）。画面を本物の長さで見るため。
        {
            "part": Part.CHILDCARE,
            "type": ActionType.CHECK_AVAILABILITY,
            "draft": (
                "お世話になっております。火曜の延長保育の空きを確認させてください。\n"
                "空きがあれば、仮で押さえていただけますか。\n"
                "先日確認をお願いした木曜の延長は不要になりましたので、外してください。"
                "よろしくお願いします。"
            ),
            "basis_ids": basis("保育園・預け先"),
            "release": release,
            "ask": "空きがあれば、仮で押さえていただけますか。",
        },
    ]
    if has_errand:
        actions.append(
            {
                "part": Part.FAMILY,
                "type": ActionType.REQUEST,
                "draft": (
                    "退院、延びて火曜の見込みになった。\n"
                    "着替えを追加で持ってきてほしい！夜か朝で大丈夫。\n"
                    "前に聞かれた持ち物だけど、着替えは寝室のタンスの2段目にあるよ。"
                ),
                "basis_ids": basis("家族"),
                "ask": "着替えを追加で持ってきてほしい！",
            }
        )
    elif ledger["profile"]["supports"]:
        actions.append(
            {
                "part": Part.SUPPORT,
                "type": ActionType.CHECK_AVAILABILITY,
                "draft": "お世話になっております。火曜の迎えをお願いできる空きはありますか。",
                "basis_ids": basis("支援"),
                "ask": "火曜の迎えをお願いできる空きはありますか。",
            }
        )
    actions.append(
        {
            "part": Part.WORK,
            "type": ActionType.TELL,
            "draft": (
                "お疲れさまです。退院が延び、火曜の見込みになりました。\n"
                "まだ確定ではありません。決まりしだいご連絡します。\n"
                "会議の件は、あらためてご連絡させてください。"
            ),
            "basis_ids": basis("職場"),
            "ask": "会議の件は、あらためてご連絡させてください。",
        }
    )
    return Branch.model_validate(
        {
            "id": "branch-1",
            "condition_item_id": doctor_id,
            "candidate_days": (
                [
                    {"day": "木曜", "certainty": "見込み", "cancelled": True},
                    {"day": "火曜", "certainty": "見込み"},
                ]
                if has_story
                else []
            ),
            "outcomes": [
                {
                    "label": "採血がだめだった",
                    "clause": "だめなら月曜にもう一回とって",
                    "clause_item_id": doctor_id,
                    # 守りが次の段を落としたときの画面を見るための語。
                    "next": (
                        []
                        if "次の段なし" in said
                        else [
                            {
                                "day": "月曜",
                                "kind": NextKind.TEST,
                                "clause": "だめなら月曜にもう一回とって",
                            },
                            {
                                "day": "火曜",
                                "kind": NextKind.DISCHARGE,
                                "clause": "それで大丈夫なら火曜かな",
                            },
                        ]
                    ),
                    "actions": actions,
                }
            ],
        }
    )


def fake_read() -> ReadOutput:
    return ReadOutput(
        is_test_result=False,
        lines=["入院のしおり（架空）", f"{SLIPPERS}かかとのある靴をご用意ください。", HANDOFF],
    )


def fake_search(content: str) -> SearchOutput:
    question = re.search(r"<question>\n(.*)\n</question>", content, re.S)[1]
    docs = json.loads(re.search(r"<docs>\n(.*)\n</docs>", content, re.S)[1])
    if "スリッパ" in question:
        for doc in docs:
            if SLIPPERS in doc["text"]:
                return SearchOutput(found=True, doc_id=doc["id"], quote=SLIPPERS)
    if "受け渡し" in question:
        for doc in docs:
            if HANDOFF in doc["text"]:
                return SearchOutput(found=True, doc_id=doc["id"], quote=HANDOFF)
    return SearchOutput(found=False, doc_id=None, quote=None)


def _sentence_with(text: str, words: tuple[str, ...]) -> str | None:
    """文を「。」で区切り、語を含む最初の1文を、原文のまま返す。"""
    for sentence in re.findall(r"[^。]+。?", text):
        if any(word in sentence for word in words):
            return sentence.split("：", 1)[-1].strip()
    return None


def fake_ledger_search(content: str) -> LedgerSearchOutput:
    """④をやりとりへ広げた道。台帳の項目か資料の文を、そのまま引用する。"""
    question = re.search(r"<question>\n(.*?)\n</question>", content, re.S)[1]
    ledger = json.loads(re.search(r"<ledger>\n(.*?)\n</ledger>", content, re.S)[1])
    docs = json.loads(re.search(r"<docs>\n(.*?)\n</docs>", content, re.S)[1])
    found: list[FoundQuote] = []
    for doc in docs:
        if "スリッパ" in question and SLIPPERS in doc["text"]:
            found.append(FoundQuote(id=doc["id"], quote=SLIPPERS))
        if "受け渡し" in question and HANDOFF in doc["text"]:
            found.append(FoundQuote(id=doc["id"], quote=HANDOFF))
    if "退院" in question or "帰れる" in question:
        for item in ledger:
            if item.get("part") == "病院" and item.get("speaker") == "医師":
                quote = _sentence_with(item["text"], ("帰れる", "退院", "候補"))
                if quote:
                    found.append(FoundQuote(id=item["id"], quote=quote))
                    break
    if "夫" in question:
        husband = [item for item in ledger if item.get("speaker") == "夫"]
        if husband:
            found.append(FoundQuote(id=husband[-1]["id"], quote=husband[-1]["text"]))
    if "上司から" in question:
        boss = [item for item in ledger if item.get("part") == "職場" and item.get("summary")]
        if boss:
            found.append(FoundQuote(id=boss[-1]["id"], quote=boss[-1]["text"]))
    if "上司に" in question:
        told = [
            item for item in ledger if item.get("part") == "職場" and item.get("speaker") == "親"
        ]
        if told:
            found.append(FoundQuote(id=told[-1]["id"], quote=told[-1]["text"]))
    return LedgerSearchOutput(found=found[:3])


class FakeAI:
    def __init__(self) -> None:
        self.beta = SimpleNamespace(messages=SimpleNamespace(parse=self.parse))

    def parse(self, **kwargs: Any) -> SimpleNamespace:
        time.sleep(DELAY_SECONDS)
        content = kwargs["messages"][0]["content"]
        output_format = kwargs["output_format"]
        if output_format is ReadOutput:
            parsed: Any = fake_read()
        elif output_format is SearchOutput:
            parsed = fake_search(content)
        elif output_format is LedgerSearchOutput:
            parsed = fake_ledger_search(content)
        elif output_format is SortedItems:
            parsed = fake_intake(content)
        else:
            parsed = fake_retell(content)
        return SimpleNamespace(
            parsed_output=parsed,
            stop_reason="end_turn",
            model="fake",
            usage=SimpleNamespace(input_tokens=0, output_tokens=0),
        )


app = create_app(client_factory=FakeAI)
