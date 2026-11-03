import json
from pathlib import Path

from fastapi.testclient import TestClient

from ensemble.ailog import record_ai_call
from ensemble.config import normalize_database_url
from ensemble.main import create_app
from sqlalchemy.engine import Engine


def make_dist(tmp_path: Path) -> Path:
    (tmp_path / "index.html").write_text("<div id=root></div>", encoding="utf-8")
    (tmp_path / "a.js").write_text("1", encoding="utf-8")
    return tmp_path


def test_health(engine: Engine, tmp_path: Path) -> None:
    client = TestClient(create_app(engine, dist=tmp_path))
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_serves_screen_and_falls_back_to_index(engine: Engine, tmp_path: Path) -> None:
    client = TestClient(create_app(engine, dist=make_dist(tmp_path)))
    assert "root" in client.get("/").text
    assert client.get("/a.js").text == "1"
    assert "root" in client.get("/board/abc").text


def test_does_not_serve_files_outside_dist(engine: Engine, tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    make_dist(dist)
    (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")
    client = TestClient(create_app(engine, dist=dist))
    assert "secret" not in client.get("/..%2Fsecret.txt").text


def test_missing_build_is_404(engine: Engine, tmp_path: Path) -> None:
    client = TestClient(create_app(engine, dist=tmp_path))
    assert client.get("/").status_code == 404


def test_database_url_normalized() -> None:
    assert normalize_database_url("postgres://u:p@h/d") == "postgresql+psycopg://u:p@h/d"
    assert normalize_database_url("postgresql://u:p@h/d") == "postgresql+psycopg://u:p@h/d"
    assert normalize_database_url(None).startswith("sqlite:///")


def test_ai_log_masks_family_code(tmp_path: Path) -> None:
    path = tmp_path / "logs" / "a.jsonl"
    code = "abcdef0123456789"
    record_ai_call("ingest", {"t": "入力"}, {"items": []}, family_code=code, path=path)
    line = path.read_text(encoding="utf-8")
    assert code not in line
    assert json.loads(line)["input"]["t"] == "入力"


def test_safety_headers_and_robots(tmp_path: Path) -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    from ensemble.main import create_app

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    client = TestClient(create_app(engine, dist=tmp_path))
    for path in ("/health", "/api/board", "/robots.txt"):
        headers = client.get(path).headers
        assert headers["Referrer-Policy"] == "no-referrer"
        assert "noindex" in headers["X-Robots-Tag"]
    assert "Disallow: /" in client.get("/robots.txt").text
