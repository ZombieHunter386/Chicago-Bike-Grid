"""Tests for the /lts-network Flask route (Plan 2D Task 3)."""
from __future__ import annotations

import gzip
from pathlib import Path

from prep.lts_network_export import export_lts_network


def _make_app_with_lts_file(
    bikemap_db: Path, cache_db: Path, data_dir: Path,
):
    """Helper: create an app whose data_dir contains a freshly-built
    lts-network.geojson.gz."""
    from app.main import create_app
    export_lts_network(bikemap_db, data_dir / "lts-network.geojson.gz")
    return create_app(
        bikemap_db=bikemap_db,
        cache_db=cache_db,
        nominatim_user_agent="test/1.0",
        min_streets=1,
    )


def test_route_serves_gzipped_geojson_with_correct_headers(
    tiny_bikemap_db_with_pois: Path, tmp_path: Path,
) -> None:
    import shutil
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_target = data_dir / "bikemap.db"
    shutil.copy(tiny_bikemap_db_with_pois, db_target)

    app = _make_app_with_lts_file(db_target, tmp_path / "cache.db", data_dir)
    client = app.test_client()
    resp = client.get("/lts-network")

    assert resp.status_code == 200
    assert resp.headers["Content-Type"] == "application/geo+json"
    assert resp.headers["Content-Encoding"] == "gzip"
    # Gzip magic header.
    assert resp.data[:2] == b"\x1f\x8b"
    # And it decompresses to valid JSON.
    gzip.decompress(resp.data)  # raises on malformed


def test_route_returns_304_on_matching_etag(
    tiny_bikemap_db_with_pois: Path, tmp_path: Path,
) -> None:
    import shutil
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_target = data_dir / "bikemap.db"
    shutil.copy(tiny_bikemap_db_with_pois, db_target)

    app = _make_app_with_lts_file(db_target, tmp_path / "cache.db", data_dir)
    client = app.test_client()
    first = client.get("/lts-network")
    assert first.status_code == 200
    etag = first.headers["ETag"]

    second = client.get("/lts-network", headers={"If-None-Match": etag})
    assert second.status_code == 304


def test_route_revalidates_rather_than_hard_caching(
    tiny_bikemap_db_with_pois: Path, tmp_path: Path,
) -> None:
    """The export is replaced wholesale on every prep refresh, so a client must
    never reuse it without asking. A long max-age would strand returning
    visitors on the pre-refresh network (3-tier colors under a 4-level legend
    after the LTS migration); `no-cache` forces an ETag revalidation, which
    304s cheaply when nothing changed.
    """
    import shutil
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_target = data_dir / "bikemap.db"
    shutil.copy(tiny_bikemap_db_with_pois, db_target)

    app = _make_app_with_lts_file(db_target, tmp_path / "cache.db", data_dir)
    resp = app.test_client().get("/lts-network")

    assert resp.status_code == 200
    cache_control = resp.headers["Cache-Control"]
    assert "no-cache" in cache_control
    # Guard the specific regression: any nonzero max-age lets a browser serve
    # a stale export without revalidating.
    assert "max-age=0" in cache_control or "max-age" not in cache_control
    # Revalidation is only cheap if the validator is still there.
    assert resp.headers.get("ETag")


def test_route_returns_404_when_file_missing(
    tiny_bikemap_db_with_pois: Path, tmp_path: Path,
) -> None:
    import shutil
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_target = data_dir / "bikemap.db"
    shutil.copy(tiny_bikemap_db_with_pois, db_target)

    # Don't generate the geojson file.
    from app.main import create_app
    app = create_app(
        bikemap_db=db_target,
        cache_db=tmp_path / "cache.db",
        nominatim_user_agent="test/1.0",
        min_streets=1,
    )
    client = app.test_client()
    resp = client.get("/lts-network")
    assert resp.status_code == 404


def test_route_exempt_from_rate_limit(
    tiny_bikemap_db_with_pois: Path, tmp_path: Path,
) -> None:
    import shutil
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_target = data_dir / "bikemap.db"
    shutil.copy(tiny_bikemap_db_with_pois, db_target)

    app = _make_app_with_lts_file(db_target, tmp_path / "cache.db", data_dir)
    client = app.test_client()
    for _ in range(75):
        resp = client.get("/lts-network")
        assert resp.status_code == 200
