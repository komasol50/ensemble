"""画面の文言に、使わない言葉が入っていないこと。"""

from pathlib import Path

import pytest

from ensemble.config import ROOT
from ensemble.guards import BANNED_WORDS, MEDICAL_FIXED_TEXT

# 「パート」は下書きには自然に出るので、画面の文言だけで止める。
BANNED = (*BANNED_WORDS, "パート")
SCREEN_FILES = sorted((ROOT / "frontend" / "src").rglob("*.ts*")) + [
    ROOT / "frontend" / "index.html"
]


def test_screen_files_are_found() -> None:
    assert any(path.name == "words.ts" for path in SCREEN_FILES)
    assert any(path.name == "Board.tsx" for path in SCREEN_FILES)


@pytest.mark.parametrize("path", SCREEN_FILES, ids=lambda p: p.name)
def test_no_banned_words_on_screen(path: Path) -> None:
    # 「パートナー」は配偶者の語で、列の名前の「パート」とは別。
    text = path.read_text(encoding="utf-8").replace("パートナー", "")
    assert [word for word in BANNED if word in text] == []


def test_screen_shows_the_same_medical_text() -> None:
    words = (ROOT / "frontend" / "src" / "words.ts").read_text(encoding="utf-8")
    assert MEDICAL_FIXED_TEXT in words
