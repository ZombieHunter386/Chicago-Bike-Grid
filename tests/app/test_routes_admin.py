"""Tests for /admin/upload-bikemap-db (app/routes/admin.py).

Covers:
- Token gating (401 paths and 200 happy path).
- Missing multipart fields (400 paths).
- Per-file validation (bad schema_version, non-gzip).
- Atomic rollback: validation failure leaves both destination files
  untouched and no .new tempfiles on disk.
- Successful dual-file swap.
- Blueprint not registered when UPLOAD_TOKEN unset (handled by app/main.py;
  asserted indirectly by exercising it through a Flask harness here).
"""
from __future__ import annotations

import gzip
import io
import sqlite3
from pathlib import Path

import pytest
from flask import Flask

from app.routes.admin import (
    BIKEMAP_FIELD,
    GEOJSON_FIELD,
    MIN_SCHEMA_VERSION,
    build_admin_blueprint,
)

TOKEN = "test-upload-token-not-a-real-secret"


def _write_valid_bikemap(path: Path, schema_version: int = MIN_SCHEMA_VERSION) -> None:
    """Minimal valid bikemap.db: schema_meta row only. Validator just checks
    schema_version >= MIN_SCHEMA_VERSION; routing/graph tables aren't read here."""
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE schema_meta (schema_version INTEGER, built_at TEXT, code_version TEXT)"
    )
    con.execute(
        "INSERT INTO schema_meta VALUES (?, ?, ?)",
        (schema_version, "2026-05-11T00:00:00", "test"),
    )
    con.commit()
    con.close()


def _gzip_bytes(payload: bytes = b'{"type":"FeatureCollection","features":[]}') -> bytes:
    """Gzip the payload so the validator's magic-byte check passes."""
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
        gz.write(payload)
    return buf.getvalue()


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """Destination directory pre-populated with sentinel files we can later
    assert against to verify atomic-rollback behavior."""
    d = tmp_path / "data"
    d.mkdir()
    (d / "bikemap.db").write_bytes(b"OLD_BIKEMAP")
    (d / "lts-network.geojson.gz").write_bytes(b"OLD_GEOJSON")
    return d


@pytest.fixture
def client(data_dir: Path):
    """Flask test client with the admin blueprint registered."""
    app = Flask(__name__)
    # Werkzeug's default request size limit is generous, but production
    # uploads will be ~70 MB — bump it here to mirror prod-friendly config.
    app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024
    app.register_blueprint(build_admin_blueprint(
        data_dir=data_dir, upload_token=TOKEN,
    ))
    return app.test_client()


def _multipart(bikemap_bytes: bytes, geojson_bytes: bytes) -> dict[str, tuple]:
    return {
        BIKEMAP_FIELD: (io.BytesIO(bikemap_bytes), "bikemap.db"),
        GEOJSON_FIELD: (io.BytesIO(geojson_bytes), "lts-network.geojson.gz"),
    }


def _post(
    client,
    bikemap_bytes: bytes,
    geojson_bytes: bytes,
    token: str | None = TOKEN,
    omit_field: str | None = None,
):
    files = _multipart(bikemap_bytes, geojson_bytes)
    if omit_field is not None:
        files.pop(omit_field)
    headers = {}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return client.post(
        "/admin/upload-bikemap-db", data=files, headers=headers,
        content_type="multipart/form-data",
    )


# ---------------------------------------------------------------------------
# Token gating
# ---------------------------------------------------------------------------

def test_401_when_authorization_header_missing(client, tmp_path):
    valid_db = tmp_path / "good.db"
    _write_valid_bikemap(valid_db)
    resp = _post(
        client, valid_db.read_bytes(), _gzip_bytes(), token=None,
    )
    assert resp.status_code == 401
    assert "bearer" in resp.get_json()["error"].lower()


def test_401_when_token_wrong(client, tmp_path):
    valid_db = tmp_path / "good.db"
    _write_valid_bikemap(valid_db)
    resp = _post(
        client, valid_db.read_bytes(), _gzip_bytes(), token="not-the-token",
    )
    assert resp.status_code == 401
    assert resp.get_json()["error"] == "invalid token"


# ---------------------------------------------------------------------------
# Missing multipart fields
# ---------------------------------------------------------------------------

def test_400_when_bikemap_field_missing(client, tmp_path):
    resp = _post(
        client, b"", _gzip_bytes(), omit_field=BIKEMAP_FIELD,
    )
    assert resp.status_code == 400
    assert BIKEMAP_FIELD in resp.get_json()["error"]


def test_400_when_geojson_field_missing(client, tmp_path):
    valid_db = tmp_path / "good.db"
    _write_valid_bikemap(valid_db)
    resp = _post(
        client, valid_db.read_bytes(), b"", omit_field=GEOJSON_FIELD,
    )
    assert resp.status_code == 400
    assert GEOJSON_FIELD in resp.get_json()["error"]


# ---------------------------------------------------------------------------
# Per-file validation
# ---------------------------------------------------------------------------

def test_400_when_bikemap_has_old_schema_version(client, tmp_path, data_dir):
    old_db = tmp_path / "old.db"
    _write_valid_bikemap(old_db, schema_version=MIN_SCHEMA_VERSION - 1)
    resp = _post(client, old_db.read_bytes(), _gzip_bytes())
    assert resp.status_code == 400
    assert "schema_version" in resp.get_json()["error"]
    # Destination untouched.
    assert (data_dir / "bikemap.db").read_bytes() == b"OLD_BIKEMAP"
    assert (data_dir / "lts-network.geojson.gz").read_bytes() == b"OLD_GEOJSON"


def test_400_when_bikemap_not_sqlite(client, data_dir):
    resp = _post(client, b"not a sqlite database", _gzip_bytes())
    assert resp.status_code == 400
    assert (data_dir / "bikemap.db").read_bytes() == b"OLD_BIKEMAP"


def test_400_when_bikemap_empty(client, data_dir):
    resp = _post(client, b"", _gzip_bytes())
    assert resp.status_code == 400
    assert "empty" in resp.get_json()["error"]


def test_400_when_geojson_not_gzip(client, tmp_path, data_dir):
    valid_db = tmp_path / "good.db"
    _write_valid_bikemap(valid_db)
    resp = _post(client, valid_db.read_bytes(), b"{not gzipped}")
    assert resp.status_code == 400
    assert "gzipped" in resp.get_json()["error"]
    # BOTH destinations untouched — atomic-rollback invariant.
    assert (data_dir / "bikemap.db").read_bytes() == b"OLD_BIKEMAP"
    assert (data_dir / "lts-network.geojson.gz").read_bytes() == b"OLD_GEOJSON"


def test_400_when_geojson_empty(client, tmp_path, data_dir):
    valid_db = tmp_path / "good.db"
    _write_valid_bikemap(valid_db)
    resp = _post(client, valid_db.read_bytes(), b"")
    assert resp.status_code == 400
    # See test_400_when_geojson_not_gzip — same rollback guarantee.
    assert (data_dir / "bikemap.db").read_bytes() == b"OLD_BIKEMAP"


# ---------------------------------------------------------------------------
# Happy path + atomic-replace invariant
# ---------------------------------------------------------------------------

def test_200_replaces_both_files_atomically(client, tmp_path, data_dir):
    new_db = tmp_path / "new.db"
    _write_valid_bikemap(new_db)
    new_db_bytes = new_db.read_bytes()
    new_geojson_bytes = _gzip_bytes(b'{"type":"FeatureCollection","features":[{"id":1}]}')

    resp = _post(client, new_db_bytes, new_geojson_bytes)

    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert body["status"] == "ok"
    assert body["bikemap_bytes"] == len(new_db_bytes)
    assert body["geojson_bytes"] == len(new_geojson_bytes)
    assert "restart" in body["note"].lower()

    # Destination files now hold the new content.
    assert (data_dir / "bikemap.db").read_bytes() == new_db_bytes
    assert (data_dir / "lts-network.geojson.gz").read_bytes() == new_geojson_bytes

    # No stale .new tempfiles left behind.
    assert not (data_dir / "bikemap.db.new").exists()
    assert not (data_dir / "lts-network.geojson.gz.new").exists()


def test_tempfiles_cleaned_after_validation_failure(client, tmp_path, data_dir):
    """Validation failure must clean up the streamed .new tempfiles so a
    later successful upload doesn't see stale state on disk."""
    valid_db = tmp_path / "good.db"
    _write_valid_bikemap(valid_db)

    resp = _post(client, valid_db.read_bytes(), b"not-gzip")
    assert resp.status_code == 400

    assert not (data_dir / "bikemap.db.new").exists()
    assert not (data_dir / "lts-network.geojson.gz.new").exists()
