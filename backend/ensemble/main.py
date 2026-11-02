"""APIと画面を同じサーバーから配る。"""

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import text
from sqlalchemy.engine import Engine

from .api import install_api
from .config import FRONTEND_DIST
from .db import init_db, make_engine
from .retell import make_client


def create_app(
    engine: Engine | None = None,
    dist: Path = FRONTEND_DIST,
    client_factory: Any = make_client,
) -> FastAPI:
    engine = engine or make_engine()
    init_db(engine)
    app = FastAPI(title="アンサンブル")

    @app.get("/health")
    def health() -> dict:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ok"}

    # 下の何でも受ける経路より先に登録する。
    install_api(app, engine, client_factory)

    root = dist.resolve()

    @app.get("/{path:path}", include_in_schema=False)
    def frontend(path: str) -> FileResponse:
        """ビルドした画面を返す。無いパスは index.html（画面側で振り分ける）。"""
        if path == "api" or path.startswith("api/"):
            raise HTTPException(status_code=404, detail="見つかりません")
        candidate = (root / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(root):
            return FileResponse(candidate)
        index = root / "index.html"
        if not index.is_file():
            raise HTTPException(status_code=404, detail="画面がビルドされていません")
        return FileResponse(index)

    return app


app = create_app()
