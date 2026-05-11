"""Tests for prep/upload_db.py — the client that pushes bikemap.db +
lts-network.geojson.gz to the deployed /admin/upload-bikemap-db endpoint.

Locks the wire format (multipart field names, Authorization header,
path) so a server-side rename of the endpoint shows up as a test
failure here AND in tests/app/test_routes_admin.py simultaneously.
"""
from __future__ import annotations

import gzip
import io
import sqlite3
from pathlib import Path

import pytest
import responses

from prep.upload_db import UPLOAD_PATH, upload


def _make_files(tmp_path: Path) -> tuple[Path, Path]:
    db = tmp_path / "bikemap.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE schema_meta (schema_version INTEGER)")
    con.execute("INSERT INTO schema_meta VALUES (2)")
    con.commit()
    con.close()

    gz = tmp_path / "lts-network.geojson.gz"
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as f:
        f.write(b'{"type":"FeatureCollection","features":[]}')
    gz.write_bytes(buf.getvalue())
    return db, gz


@responses.activate
def test_upload_sends_both_files_with_bearer_token(tmp_path: Path):
    db, gz = _make_files(tmp_path)
    base_url = "https://example.test"
    responses.add(
        responses.POST,
        base_url + UPLOAD_PATH,
        json={
            "status": "ok",
            "bikemap_bytes": db.stat().st_size,
            "geojson_bytes": gz.stat().st_size,
            "note": "service restart required",
        },
        status=200,
    )

    result = upload(
        db_path=db, geojson_path=gz, base_url=base_url, token="my-token",
    )

    assert result["status"] == "ok"
    assert len(responses.calls) == 1
    call = responses.calls[0]
    assert call.request.headers["Authorization"] == "Bearer my-token"

    # Multipart body includes both named fields. `responses` records the
    # raw body; field names live in the Content-Disposition headers.
    body = call.request.body
    body_bytes = body if isinstance(body, bytes) else body.read()
    assert b'name="bikemap"' in body_bytes
    assert b'name="lts_network"' in body_bytes


@responses.activate
def test_upload_raises_on_4xx(tmp_path: Path):
    db, gz = _make_files(tmp_path)
    base_url = "https://example.test"
    responses.add(
        responses.POST,
        base_url + UPLOAD_PATH,
        json={"error": "invalid token"},
        status=401,
    )

    import requests
    with pytest.raises(requests.HTTPError):
        upload(
            db_path=db, geojson_path=gz, base_url=base_url, token="bad",
        )


@responses.activate
def test_base_url_trailing_slash_does_not_double_slash(tmp_path: Path):
    db, gz = _make_files(tmp_path)
    base_url = "https://example.test/"
    responses.add(
        responses.POST,
        "https://example.test" + UPLOAD_PATH,  # NOT https://example.test//admin/...
        json={"status": "ok", "bikemap_bytes": 0, "geojson_bytes": 0, "note": ""},
        status=200,
    )
    upload(db_path=db, geojson_path=gz, base_url=base_url, token="t")
    # If trailing-slash handling regresses, the responses mock above won't
    # match and the call will 404 (responses raises ConnectionError).
    assert len(responses.calls) == 1
