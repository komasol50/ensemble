"""画面の文言に、使わない言葉が入っていないこと。"""

from pathlib import Path

import pytest

from ensemble.config import ROOT

BANNED = (
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
    "パート",
)
SCREEN_FILES = sorted((ROOT / "frontend" / "src").rglob("*.ts*")) + [
    ROOT / "frontend" / "index.html"
]


def test_screen_files_are_found() -> None:
    assert any(path.name == "words.ts" for path in SCREEN_FILES)
    assert any(path.name == "Board.tsx" for path in SCREEN_FILES)


@pytest.mark.parametrize("path", SCREEN_FILES, ids=lambda p: p.name)
def test_no_banned_words_on_screen(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert [word for word in BANNED if word in text] == []
