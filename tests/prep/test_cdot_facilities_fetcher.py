"""CDOT bike-facility fetch + parse (Phase 2b).

Two ArcGIS FeatureServer layers (mirrors the prep/fetchers/hin.py pattern):
  - on-street `Bikeway_Network_2024_Final_Public`, facility type in `BIKE_DSPLY`
  - off-street `Trails_Network_2024_11_18`, whole layer -> tier 1 (field unused)

Each parsed CdotFacility carries the facility_type (on-street) or off_street=True
(trails). The on-street facility-type strings must resolve through the classifier.
"""

import json
from pathlib import Path

import responses

from prep.fetchers.cdot_facilities import (
    CdotFacilitiesFetcher,
    CdotFacility,
    parse_cdot_facilities,
)
from prep.scoring.classifier import cdot_lts_for_facility

ON_URL = "https://example.com/services/Bikeway_Network/FeatureServer/0"
OFF_URL = "https://example.com/services/Trails/FeatureServer/0"


def _fetcher() -> CdotFacilitiesFetcher:
    return CdotFacilitiesFetcher(
        on_street_url=ON_URL,
        facility_type_field="BIKE_DSPLY",
        trails_url=OFF_URL,
    )


def _add_layer_responses(fixtures_dir: Path) -> None:
    on = json.loads((fixtures_dir / "cdot_on_street_response.json").read_text())
    off = json.loads((fixtures_dir / "cdot_off_street_response.json").read_text())
    responses.add(responses.GET, f"{ON_URL}/query", json=on, status=200)
    responses.add(responses.GET, f"{OFF_URL}/query", json=off, status=200)


@responses.activate
def test_cdot_fetcher_writes_two_geojson_files(cache_dir: Path, fixtures_dir: Path) -> None:
    _add_layer_responses(fixtures_dir)

    result = _fetcher().fetch(cache_dir)

    assert result.status == "OK"
    assert result.record_count == 5  # 3 on-street + 2 off-street
    on_path = cache_dir / "cdot_on_street.geojson"
    off_path = cache_dir / "cdot_off_street.geojson"
    assert on_path.exists() and off_path.exists()
    on_geo = json.loads(on_path.read_text())
    assert on_geo["type"] == "FeatureCollection"
    assert len(on_geo["features"]) == 3
    assert on_geo["features"][0]["geometry"]["type"] == "LineString"


@responses.activate
def test_cdot_fetcher_handles_http_error(cache_dir: Path) -> None:
    responses.add(responses.GET, f"{ON_URL}/query", status=503)
    responses.add(responses.GET, f"{OFF_URL}/query", status=200, json={"features": []})

    result = _fetcher().fetch(cache_dir)

    assert result.status == "FAIL"
    assert any("503" in w for w in result.warnings)


@responses.activate
def test_parse_cdot_facilities(cache_dir: Path, fixtures_dir: Path) -> None:
    _add_layer_responses(fixtures_dir)
    _fetcher().fetch(cache_dir)

    facilities = list(
        parse_cdot_facilities(
            on_street_path=cache_dir / "cdot_on_street.geojson",
            off_street_path=cache_dir / "cdot_off_street.geojson",
            facility_type_field="BIKE_DSPLY",
        )
    )

    assert len(facilities) == 5
    assert all(isinstance(f, CdotFacility) for f in facilities)

    on_street = [f for f in facilities if not f.off_street]
    off_street = [f for f in facilities if f.off_street]
    assert len(on_street) == 3
    assert len(off_street) == 2

    # on-street facility types resolve through the classifier. The fixture holds
    # PROTECTED -> 1, BUFFERED -> 2 and SHARED -> None (a sharrow earns no
    # override under improve-only semantics; see design §3.3).
    overrides = sorted(
        (cdot_lts_for_facility(f.facility_type) for f in on_street),
        key=lambda v: (v is None, v),
    )
    assert overrides == [1, 2, None]

    # off-street facilities carry no facility_type but still have geometry
    assert all(f.facility_type is None and f.geometry for f in off_street)
