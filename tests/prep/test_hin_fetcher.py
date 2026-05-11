# tests/prep/test_hin_fetcher.py
import json
from pathlib import Path

import pytest
import responses

from prep.fetchers.hin import HinFetcher


@pytest.fixture
def segments_url() -> str:
    return "https://example.com/services/HIN_Segments/FeatureServer/0"


@pytest.fixture
def intersections_url() -> str:
    return "https://example.com/services/HIN_Intersections/FeatureServer/0"


@responses.activate
def test_hin_fetcher_writes_two_geojson_files(
    cache_dir: Path,
    fixtures_dir: Path,
    segments_url: str,
    intersections_url: str,
) -> None:
    seg_payload = json.loads((fixtures_dir / "hin_segments_response.json").read_text())
    responses.add(
        responses.GET,
        f"{segments_url}/query",
        json=seg_payload,
        status=200,
    )
    int_payload = json.loads((fixtures_dir / "hin_intersections_response.json").read_text())
    responses.add(
        responses.GET,
        f"{intersections_url}/query",
        json=int_payload,
        status=200,
    )

    fetcher = HinFetcher(
        segments_url=segments_url,
        intersections_url=intersections_url,
    )
    result = fetcher.fetch(cache_dir)

    assert result.status == "OK"
    assert result.record_count == 3  # 2 segments + 1 intersection
    seg_path = cache_dir / "hin_segments.geojson"
    int_path = cache_dir / "hin_intersections.geojson"
    assert seg_path.exists()
    assert int_path.exists()

    seg_geo = json.loads(seg_path.read_text())
    assert seg_geo["type"] == "FeatureCollection"
    assert len(seg_geo["features"]) == 2
    assert seg_geo["features"][0]["properties"]["STNAME"] == "WESTERN AVE"
    assert seg_geo["features"][0]["geometry"]["type"] == "LineString"

    int_geo = json.loads(int_path.read_text())
    assert int_geo["type"] == "FeatureCollection"
    assert int_geo["features"][0]["geometry"]["type"] == "Point"


@responses.activate
def test_hin_fetcher_handles_http_error(
    cache_dir: Path,
    segments_url: str,
    intersections_url: str,
) -> None:
    responses.add(responses.GET, f"{segments_url}/query", status=503)
    responses.add(responses.GET, f"{intersections_url}/query", status=200, json={"features": []})

    fetcher = HinFetcher(
        segments_url=segments_url,
        intersections_url=intersections_url,
    )
    result = fetcher.fetch(cache_dir)

    assert result.status == "FAIL"
    assert any("503" in w for w in result.warnings)


@responses.activate
def test_hin_fetcher_paginates_until_transfer_limit_clears(
    cache_dir: Path,
    segments_url: str,
    intersections_url: str,
) -> None:
    responses.add(
        responses.GET,
        f"{segments_url}/query",
        json={
            "spatialReference": {"wkid": 4326},
            "exceededTransferLimit": True,
            "features": [
                {"attributes": {"OBJECTID": 1, "STNAME": "A", "MODE_BIKE": 1, "MODE_PED": 0, "SEVERITY_RANK": 3},
                 "geometry": {"paths": [[[-87.7, 41.9], [-87.6, 41.9]]]}},
                {"attributes": {"OBJECTID": 2, "STNAME": "B", "MODE_BIKE": 1, "MODE_PED": 1, "SEVERITY_RANK": 4},
                 "geometry": {"paths": [[[-87.7, 41.91], [-87.6, 41.91]]]}},
            ],
        },
        status=200,
    )
    responses.add(
        responses.GET,
        f"{segments_url}/query",
        json={
            "spatialReference": {"wkid": 4326},
            "features": [
                {"attributes": {"OBJECTID": 3, "STNAME": "C", "MODE_BIKE": 0, "MODE_PED": 1, "SEVERITY_RANK": 2},
                 "geometry": {"paths": [[[-87.7, 41.92], [-87.6, 41.92]]]}},
            ],
        },
        status=200,
    )
    responses.add(
        responses.GET,
        f"{intersections_url}/query",
        json={"spatialReference": {"wkid": 4326}, "features": []},
        status=200,
    )

    fetcher = HinFetcher(
        segments_url=segments_url, intersections_url=intersections_url,
    )
    result = fetcher.fetch(cache_dir)
    assert result.status == "OK"

    seg_geo = json.loads((cache_dir / "hin_segments.geojson").read_text())
    assert len(seg_geo["features"]) == 3


@responses.activate
def test_hin_fetcher_raises_on_unexpected_spatial_reference(
    cache_dir: Path,
    segments_url: str,
    intersections_url: str,
) -> None:
    responses.add(
        responses.GET,
        f"{segments_url}/query",
        json={
            "spatialReference": {"wkid": 3435},
            "features": [
                {"attributes": {"OBJECTID": 1, "STNAME": "A"},
                 "geometry": {"paths": [[[1.1e6, 1.9e6], [1.2e6, 1.9e6]]]}},
            ],
        },
        status=200,
    )
    responses.add(
        responses.GET,
        f"{intersections_url}/query",
        json={"spatialReference": {"wkid": 4326}, "features": []},
        status=200,
    )

    fetcher = HinFetcher(
        segments_url=segments_url, intersections_url=intersections_url,
    )
    result = fetcher.fetch(cache_dir)
    assert result.status == "FAIL"
    assert any("spatial reference" in w.lower() or "3435" in w for w in result.warnings)
