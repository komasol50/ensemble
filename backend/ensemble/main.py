"""APIと画面を同じサーバーから配る。"""

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import text
from sqlalchemy.engine import Engine

from .config import FRONTEND_DIST
from .db import init_db, make_engine


def create_app(engine: Engine | None = None, dist: Path = FRONTEND_DIST) -> FastAPI:
    engine = engine or make_engine()
    init_db(engine)
    app = FastAPI(title="アンサンブル")

    @app.get("/health")
    def health() -> dict:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ok"}

    root = dist.resolve()

    @app.get("/{path:path}", include_in_schema=False)
    def frontend(path: str) -> FileResponse:
        """ビルドした画面を返す。無いパスは index.html（画面側で振り分ける）。"""
        candidate = (root / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(root):
            return FileResponse(candidate)
        index = root / "index.html"
        if not index.is_file():
            raise HTTPException(status_code=404, detail="画面がビルドされていません")
        return FileResponse(index)

    return app


app = create_app()
