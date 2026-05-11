# tests/prep/test_cdot_sanity_fetcher.py
import json
from pathlib import Path

import responses

from prep.fetchers.cdot_sanity import CdotBikewaysFetcher


@responses.activate
def test_cdot_sanity_fetcher_writes_geojson(
    cache_dir: Path,
    fixtures_dir: Path,
) -> None:
    payload = json.loads((fixtures_dir / "cdot_bikeways_response.json").read_text())
    responses.add(
        responses.GET,
        "https://data.cityofchicago.org/resource/3w5d-sru8.json",
        json=payload,
        status=200,
    )

    fetcher = CdotBikewaysFetcher(domain="data.cityofchicago.org", dataset_id="3w5d-sru8")
    result = fetcher.fetch(cache_dir)

    assert result.status == "OK"
    assert result.record_count == 2
    out = cache_dir / "cdot_bikeways.geojson"
    assert out.exists()

    geo = json.loads(out.read_text())
    assert geo["type"] == "FeatureCollection"
    assert len(geo["features"]) == 2
    assert geo["features"][0]["properties"]["facility_t"] == "PROTECTED BIKE LANE"
    assert geo["features"][0]["geometry"]["type"] == "MultiLineString"


@responses.activate
def test_cdot_sanity_fetcher_handles_wkt_geometry_format(cache_dir: Path) -> None:
    """Some CDP datasets return `the_geom` as WKT instead of GeoJSON."""
    payload = [
        {
            "objectid": "1",
            "facility_t": "PROTECTED BIKE LANE",
            "street": "MILWAUKEE AVE",
            "the_geom": "MULTILINESTRING ((-87.665 41.903, -87.667 41.910))",
        },
    ]
    responses.add(
        responses.GET,
        "https://data.cityofchicago.org/resource/3w5d-sru8.json",
        json=payload,
        status=200,
    )
    fetcher = CdotBikewaysFetcher(domain="data.cityofchicago.org", dataset_id="3w5d-sru8")
    result = fetcher.fetch(cache_dir)
    assert result.status == "OK"
    assert result.record_count == 1
    geo = json.loads((cache_dir / "cdot_bikeways.geojson").read_text())
    assert geo["features"][0]["geometry"]["type"] == "MultiLineString"
