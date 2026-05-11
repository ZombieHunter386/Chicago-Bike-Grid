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


def test_create_app_raises_when_bikemap_missing(
    tmp_path: Path, monkeypatch,
) -> None:
    """Without UPLOAD_TOKEN, a missing bikemap.db is a misconfiguration
    and we fail loudly. (With UPLOAD_TOKEN, bootstrap mode kicks in —
    covered by test_create_app_returns_admin_only_when_bikemap_missing.)"""
    import pytest

    from app.main import create_app

    monkeypatch.delenv("UPLOAD_TOKEN", raising=False)
    with pytest.raises(FileNotFoundError):
        create_app(
            bikemap_db=tmp_path / "missing.db",
            cache_db=tmp_path / "cache.db",
            nominatim_user_agent="test/1.0",
            min_streets=1,
        )


def test_create_app_returns_admin_only_when_bikemap_missing_and_token_set(
    tmp_path: Path, monkeypatch,
) -> None:
    """First-boot bootstrap branch: empty disk + UPLOAD_TOKEN → stub app
    exposing only /admin/upload-bikemap-db and a 200 /health."""
    from app.main import create_app

    monkeypatch.setenv("UPLOAD_TOKEN", "test-token")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    app = create_app(
        bikemap_db=data_dir / "bikemap.db",
        cache_db=tmp_path / "cache.db",
        nominatim_user_agent="test/1.0",
        min_streets=1,
    )
    client = app.test_client()

    # /health must return 200 (else Render won't route traffic).
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "awaiting_bootstrap"

    # /admin/upload-bikemap-db is wired (401 because no auth header).
    resp = client.post("/admin/upload-bikemap-db")
    assert resp.status_code == 401

    # Routing endpoints are NOT registered in this mode.
    resp = client.post("/routes", json={})
    assert resp.status_code == 404


def test_admin_only_mode_skipped_when_token_unset(
    tmp_path: Path, monkeypatch,
) -> None:
    """Empty token (env-var set but empty after .strip()) must NOT trigger
    bootstrap mode — same as token-unset, fail loudly."""
    import pytest

    from app.main import create_app

    monkeypatch.setenv("UPLOAD_TOKEN", "   ")
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
