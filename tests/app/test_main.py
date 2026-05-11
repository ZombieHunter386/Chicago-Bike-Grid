"""Tests for the Flask app factory."""
from pathlib import Path


def test_create_app_returns_flask_app_with_blueprints(
    tiny_bikemap_db_with_pois: Path, tmp_path: Path,
) -> None:
    from flask import Flask

    from app.main import create_app

    cache_db = tmp_path / "cache.db"
    app = create_app(
        bikemap_db=tiny_bikemap_db_with_pois,
        cache_db=cache_db,
        nominatim_user_agent="test/1.0",
        min_streets=1,
    )
    assert isinstance(app, Flask)
    # Blueprints registered: routes, pois, treatments, geocode, gap_analysis.
    bp_names = {bp.name for bp in app.blueprints.values()}
    assert {"routes", "pois", "treatments", "geocode", "gap_analysis"}.issubset(bp_names)


def test_health_endpoint_returns_200_when_loaded(
    tiny_bikemap_db_with_pois: Path, tmp_path: Path,
) -> None:
    from app.main import create_app

    app = create_app(
        bikemap_db=tiny_bikemap_db_with_pois,
        cache_db=tmp_path / "cache.db",
        nominatim_user_agent="test/1.0",
        min_streets=1,
    )
    client = app.test_client()
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "ok"


def test_create_app_raises_when_bikemap_missing(tmp_path: Path) -> None:
    import pytest

    from app.main import create_app

    with pytest.raises(FileNotFoundError):
        create_app(
            bikemap_db=tmp_path / "missing.db",
            cache_db=tmp_path / "cache.db",
            nominatim_user_agent="test/1.0",
            min_streets=1,
        )


def test_create_app_raises_on_insufficient_streets(
    tiny_bikemap_db: Path, tmp_path: Path,
) -> None:
    """Startup validation: streets row count must meet min_streets threshold."""
    import pytest

    from app.main import create_app

    with pytest.raises(RuntimeError, match="streets"):
        create_app(
            bikemap_db=tiny_bikemap_db,
            cache_db=tmp_path / "cache.db",
            nominatim_user_agent="test/1.0",
            min_streets=10000,  # synthetic DB has only 5
        )


def test_proxyfix_wired_for_x_forwarded_for(
    tiny_bikemap_db_with_pois: Path, tmp_path: Path,
) -> None:
    """Behind Render's proxy, request.remote_addr should be derived from
    X-Forwarded-For (Spec §3.10 — per-IP rate limiting must work in production).

    Without ProxyFix wrapping the WSGI app, request.remote_addr resolves to
    Render's edge IP for every request, collapsing all external clients into
    one shared rate-limit bucket.
    """
    from flask import request

    from app.main import create_app

    app = create_app(
        bikemap_db=tiny_bikemap_db_with_pois,
        cache_db=tmp_path / "cache.db",
        nominatim_user_agent="test/1.0",
        min_streets=1,
    )
    captured: dict[str, str | None] = {}

    @app.before_request
    def _capture():  # type: ignore[no-untyped-def]
        captured["remote_addr"] = request.remote_addr

    client = app.test_client()
    client.get("/health", headers={"X-Forwarded-For": "203.0.113.42"})
    assert captured["remote_addr"] == "203.0.113.42"


def test_root_serves_spa_shell(tiny_bikemap_db_with_pois: Path, tmp_path: Path) -> None:
    from app.main import create_app
    app = create_app(
        bikemap_db=tiny_bikemap_db_with_pois,
        cache_db=tmp_path / "cache.db",
        nominatim_user_agent="test/1.0",
        min_streets=1,
    )
    client = app.test_client()
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"<title>Chicago Bike Advocacy Map</title>" in resp.data


def test_create_app_raises_on_old_schema_version(tmp_path: Path) -> None:
    """Fix E: a bikemap.db with schema_version < MIN_SCHEMA_VERSION fails
    fast at startup rather than producing OperationalError at request time."""
    import sqlite3

    import pytest

    from app.main import create_app

    db_path = tmp_path / "old_schema.db"
    # Hand-build a stub DB with schema_version=1 (the pre-migration version).
    con = sqlite3.connect(db_path)
    con.executescript("""
        CREATE TABLE schema_meta (schema_version INTEGER NOT NULL,
                                  built_at TEXT NOT NULL,
                                  code_version TEXT);
        CREATE TABLE streets (osm_id INTEGER PRIMARY KEY, lts INTEGER NOT NULL);
        INSERT INTO schema_meta (schema_version, built_at, code_version)
            VALUES (1, '2025-01-01', 'old');
    """)
    con.commit()
    con.close()

    with pytest.raises(RuntimeError, match="schema_version"):
        create_app(
            bikemap_db=db_path,
            cache_db=tmp_path / "cache.db",
            nominatim_user_agent="test/1.0",
            min_streets=0,
        )


def test_explore_route_serves_explorer_shell(
    tiny_bikemap_db_with_pois: Path, tmp_path: Path,
) -> None:
    from app.main import create_app
    app = create_app(
        bikemap_db=tiny_bikemap_db_with_pois,
        cache_db=tmp_path / "cache.db",
        nominatim_user_agent="test/1.0",
        min_streets=1,
    )
    client = app.test_client()
    resp = client.get("/explore")
    assert resp.status_code == 200
    assert b"<title>Chicago LTS Data Explorer</title>" in resp.data
    assert b'src="/static/explore.js"' in resp.data
