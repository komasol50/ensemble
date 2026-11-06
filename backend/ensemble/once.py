"""同じ要求の送り直しで、AIを2回呼ばないための記憶。

スマホは裏に回ると通信を切るので、端末は同じ要求を送り直す。実行中なら終わるのを待ち、
終わっていれば同じ結果を返す。失敗は覚えない（押し直せば、もう一度実行する）。
1プロセスの記憶で足りる（再起動をまたぐ送り直しは、まれなので許す）。
"""

from collections import OrderedDict
from collections.abc import Callable, Hashable
from concurrent.futures import Future
from threading import Lock
from typing import Any


class OnceCache:
    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._lock = Lock()
        self._calls: OrderedDict[Hashable, Future[Any]] = OrderedDict()

    def run(
        self,
        key: Hashable,
        call: Callable[[], Any],
        keep: Callable[[Any], bool] = lambda _: True,
    ) -> Any:
        """keep が偽の結果は覚えない。例外にならない失敗（案を出せなかった）も、押し直せば実行する。"""
        with self._lock:
            future = self._calls.get(key)
            is_first = future is None
            if future is None:
                future = Future()
                self._calls[key] = future
                self._forget_old()
        if not is_first:
            return future.result()
        try:
            result = call()
        except BaseException as error:
            with self._lock:
                self._calls.pop(key, None)
            future.set_exception(error)
            raise
        if not keep(result):
            with self._lock:
                self._calls.pop(key, None)
        # 実行中に待っていた送り直しには、同じ結果を返す。
        future.set_result(result)
        return result

    def _forget_old(self) -> None:
        """古い順に忘れる。実行中のものは、待っている人がいるので残す。"""
        for key in list(self._calls):
            if len(self._calls) <= self._limit:
                return
            if self._calls[key].done():
                del self._calls[key]
