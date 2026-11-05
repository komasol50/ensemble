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
from ensemble.reading import ReadOutput, SearchOutput  # noqa: E402

DELAY_SECONDS = 1.0  # 「仕分け中」を見届けるための待ち

# 偽の応答を本物の呼び出しの記録に混ぜない。
ensemble.intake.record_ai_call = lambda *a, **k: None
ensemble.retell.record_ai_call = lambda *a, **k: None
ensemble.reading.record_ai_call = lambda *a, **k: None

SLIPPERS = "スリッパはお控えください。"


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
    if "売店" in text:
        return SortedItems(items=[sorted_item(text, kind=Kind.ACTION, part=Part.PARENT)])
    return SortedItems(items=[sorted_item(text)])


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
    has_errand = bool(ledger["profile"]["errand_people"])

    def first_of(part: str) -> str | None:
        return next((item["id"] for item in items if item.get("part") == part), None)

    def basis(part: str) -> list[str]:
        found = first_of(part)
        return [entry for entry in dict.fromkeys([found, doctor_id, trigger]) if entry]

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
                    "actions": actions,
                }
            ],
        }
    )


def fake_read() -> ReadOutput:
    return ReadOutput(
        is_test_result=False,
        lines=["入院のしおり（架空）", f"{SLIPPERS}かかとのある靴をご用意ください。"],
    )


def fake_search(content: str) -> SearchOutput:
    question = re.search(r"<question>\n(.*)\n</question>", content, re.S)[1]
    docs = json.loads(re.search(r"<docs>\n(.*)\n</docs>", content, re.S)[1])
    if "スリッパ" in question:
        for doc in docs:
            if SLIPPERS in doc["text"]:
                return SearchOutput(found=True, doc_id=doc["id"], quote=SLIPPERS)
    return SearchOutput(found=False, doc_id=None, quote=None)


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
