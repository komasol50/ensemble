import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

from ensemble.db import init_db


@pytest.fixture
def engine() -> Engine:
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    init_db(eng)
    return eng


@pytest.fixture(autouse=True)
def no_ai_log(monkeypatch: pytest.MonkeyPatch) -> None:
    """テストの呼び出しを、本物の記録に混ぜない。"""
    import ensemble.api
    import ensemble.intake
    import ensemble.reading
    import ensemble.retell

    monkeypatch.setattr(ensemble.intake, "record_ai_call", lambda *a, **k: None)
    monkeypatch.setattr(ensemble.retell, "record_ai_call", lambda *a, **k: None)
    monkeypatch.setattr(ensemble.reading, "record_ai_call", lambda *a, **k: None)
    monkeypatch.setattr(ensemble.api, "forget_family", lambda *a, **k: None)
