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
from ensemble.ledger import ActionType, Branch, Certainty, Kind, Medical, Part  # noqa: E402
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
    doctor = re.search(r'"id": "([ab]-07)"', content)
    if "失敗" in text:
        return None
    if "だめでした" in text:
        return SortedItems(
            items=[
                sorted_item(
                    text,
                    certainty=Certainty.CONFIRMED,
                    medical=Medical.INFO,
                    refers_to=doctor[1],
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
    trigger = re.search(r"trigger_item_id: (\S+)", content)[1]
    return Branch.model_validate(
        {
            "id": "branch-1",
            "condition_item_id": "a-07",
            "candidate_days": [
                {"day": "木曜", "certainty": "見込み", "cancelled": True},
                {"day": "火曜", "certainty": "見込み"},
            ],
            "outcomes": [
                {
                    "label": "採血がだめだった",
                    "actions": [
                        {
                            "part": Part.CHILDCARE,
                            "type": ActionType.CHECK_AVAILABILITY,
                            "draft": "木曜は取り消して、火曜の延長の空きを確認させてください。",
                            "basis_ids": ["a-05", "a-07", trigger],
                            "release": ["a-05"],
                        },
                        {
                            "part": Part.FAMILY,
                            "type": ActionType.REQUEST,
                            "draft": "着替え3日分を持ってきてください。",
                            "basis_ids": ["a-02", trigger],
                        },
                        {
                            "part": Part.WORK,
                            "type": ActionType.TELL,
                            "draft": "お疲れさまです。付き添いが延びることになりました。会議には出られないので、資料は共有フォルダに置いておきます。",
                            "basis_ids": ["a-06", trigger],
                        },
                    ],
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
