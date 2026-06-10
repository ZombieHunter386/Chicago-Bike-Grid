"""Tests for the LTS-network static-file exporter (Plan 2D Task 1)."""
from __future__ import annotations

import gzip
import json
from pathlib import Path

from prep.lts_network_export import export_lts_network


def test_export_produces_valid_gzipped_geojson(
    tiny_bikemap_db_with_pois: Path, tmp_path: Path,
) -> None:
    out_path = tmp_path / "lts-network.geojson.gz"
    size = export_lts_network(tiny_bikemap_db_with_pois, out_path)

    assert out_path.exists()
    assert size == out_path.stat().st_size
    assert size > 0

    decompressed = gzip.decompress(out_path.read_bytes())
    fc = json.loads(decompressed)
    assert fc["type"] == "FeatureCollection"

    streets = [f for f in fc["features"] if f["geometry"]["type"] == "LineString"]
    points = [f for f in fc["features"] if f["geometry"]["type"] == "Point"]
    # tiny_bikemap_db_with_pois has 5 streets. Intersection points are no longer
    # exported (payload reduction): in the full city they were 203k points / ~33%
    # of the payload, and ~99% shared the same lts_approach=3 — uniform-red noise
    # that bloated the /explore download without adding signal.
    assert len(streets) == 5
    assert len(points) == 0


def test_export_street_features_have_expected_schema(
    tiny_bikemap_db_with_pois: Path, tmp_path: Path,
) -> None:
    out_path = tmp_path / "lts-network.geojson.gz"
    export_lts_network(tiny_bikemap_db_with_pois, out_path)
    fc = json.loads(gzip.decompress(out_path.read_bytes()))
    streets = [f for f in fc["features"] if f["geometry"]["type"] == "LineString"]
    for f in streets:
        assert f["properties"]["lts"] in {1, 2, 3}
        assert isinstance(f["properties"]["on_hin"], bool)
        for lon, lat in f["geometry"]["coordinates"]:
            assert -88 < lon < -87, "lon outside Chicago range"
            assert 41 < lat < 42, "lat outside Chicago range"


def test_export_rounds_coordinates_to_5_decimals(
    tiny_bikemap_db_with_pois: Path, tmp_path: Path,
) -> None:
    """The fixture inserts streets whose endpoints sit at exact known
    coordinates; after export they should match to 5 decimal places."""
    out_path = tmp_path / "lts-network.geojson.gz"
    export_lts_network(tiny_bikemap_db_with_pois, out_path)
    fc = json.loads(gzip.decompress(out_path.read_bytes()))
    streets = [f for f in fc["features"] if f["geometry"]["type"] == "LineString"]
    all_coords = {tuple(c) for f in streets for c in f["geometry"]["coordinates"]}
    # tiny fixture's streets share endpoints with v100 (-87.680, 41.940) and
    # v200 (-87.675, 41.945) — round to 5 decimals == itself.
    assert (-87.68, 41.94) in all_coords  # v100
    assert (-87.675, 41.945) in all_coords  # v200


def test_export_is_atomic(
    tiny_bikemap_db_with_pois: Path, tmp_path: Path,
) -> None:
    """No .tmp file should remain after a successful run."""
    out_path = tmp_path / "lts-network.geojson.gz"
    export_lts_network(tiny_bikemap_db_with_pois, out_path)
    assert not (tmp_path / "lts-network.geojson.gz.tmp").exists()
