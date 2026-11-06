"""同じ要求の送り直しで、AIを2回呼ばない。"""

from threading import Barrier, Thread

import pytest

from ensemble.once import OnceCache


def test_second_call_returns_the_first_result() -> None:
    cache = OnceCache(10)
    calls: list[int] = []

    def work() -> str:
        calls.append(1)
        return "結果"

    assert cache.run("k", work) == "結果"
    assert cache.run("k", work) == "結果"
    assert len(calls) == 1


def test_call_in_progress_is_waited_for() -> None:
    """スマホが送り直したとき、最初の呼び出しがまだ動いていても2回呼ばない。"""
    cache = OnceCache(10)
    started = Barrier(2)
    release = Barrier(2)
    calls: list[int] = []
    results: list[str] = []

    def slow() -> str:
        calls.append(1)
        started.wait(timeout=5)
        release.wait(timeout=5)
        return "結果"

    first = Thread(target=lambda: results.append(cache.run("k", slow)))
    first.start()
    started.wait(timeout=5)
    second = Thread(target=lambda: results.append(cache.run("k", slow)))
    second.start()
    release.wait(timeout=5)
    first.join(timeout=5)
    second.join(timeout=5)
    assert results == ["結果", "結果"]
    assert len(calls) == 1


def test_failure_is_not_remembered() -> None:
    cache = OnceCache(10)

    def fail() -> str:
        raise ValueError("だめ")

    with pytest.raises(ValueError):
        cache.run("k", fail)
    assert cache.run("k", lambda: "次は通る") == "次は通る"


def test_old_results_are_forgotten() -> None:
    cache = OnceCache(2)
    for key in ("a", "b", "c"):
        cache.run(key, lambda: key)
    calls: list[int] = []
    cache.run("a", lambda: calls.append(1) or "again")
    assert calls == [1]


def test_a_result_that_should_not_be_kept_runs_again() -> None:
    """keep が偽の結果は覚えない。覚えるのは keep が真の結果だけ。"""
    cache = OnceCache(10)
    results = iter([None, "ok", "never"])
    keep = lambda result: result is not None  # noqa: E731
    assert cache.run("k", lambda: next(results), keep=keep) is None
    assert cache.run("k", lambda: next(results), keep=keep) == "ok"
    assert cache.run("k", lambda: next(results), keep=keep) == "ok"
