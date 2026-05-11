# tests/prep/test_pois_cdp_fetcher.py
import json
from pathlib import Path

import responses

from prep.fetchers.pois_cdp import CdpPoisFetcher


@responses.activate
def test_cdp_pois_fetcher_writes_two_geojson_files(
    cache_dir: Path,
    fixtures_dir: Path,
) -> None:
    alderman = json.loads((fixtures_dir / "cdp_alderman_offices.json").read_text())
    libraries = json.loads((fixtures_dir / "cdp_libraries.json").read_text())
    responses.add(
        responses.GET,
        "https://data.cityofchicago.org/resource/htai-wnw4.json",
        json=alderman,
        status=200,
    )
    responses.add(
        responses.GET,
        "https://data.cityofchicago.org/resource/x8fc-8rcq.json",
        json=libraries,
        status=200,
    )

    fetcher = CdpPoisFetcher(
        domain="data.cityofchicago.org",
        alderman_dataset_id="htai-wnw4",
        library_dataset_id="x8fc-8rcq",
    )
    result = fetcher.fetch(cache_dir)

    assert result.status == "OK"
    assert result.record_count == 3  # 2 alderman + 1 library

    aldr_path = cache_dir / "cdp_alderman_offices.geojson"
    lib_path = cache_dir / "cdp_libraries.geojson"
    assert aldr_path.exists()
    assert lib_path.exists()

    aldr_geo = json.loads(aldr_path.read_text())
    assert aldr_geo["features"][0]["properties"]["ward"] == "1"
    assert aldr_geo["features"][0]["geometry"]["coordinates"] == [-87.694, 41.910]

    lib_geo = json.loads(lib_path.read_text())
    assert lib_geo["features"][0]["properties"]["name_"] == "Lincoln Park"


@responses.activate
def test_cdp_pois_fetcher_handles_separate_lat_lng_columns(cache_dir: Path) -> None:
    """Some CDP datasets emit latitude+longitude as separate string columns."""
    alderman = [
        {
            "ward": "5",
            "alderman": "Test Alder",
            "address": "1 N State St",
            "latitude": "41.883",
            "longitude": "-87.628",
        },
    ]
    libraries = [
        {
            "name_": "Test Library",
            "address": "1 W Foo Pl",
            "latitude": "41.900",
            "longitude": "-87.650",
        },
    ]
    responses.add(
        responses.GET,
        "https://data.cityofchicago.org/resource/htai-wnw4.json",
        json=alderman, status=200,
    )
    responses.add(
        responses.GET,
        "https://data.cityofchicago.org/resource/x8fc-8rcq.json",
        json=libraries, status=200,
    )
    fetcher = CdpPoisFetcher(
        domain="data.cityofchicago.org",
        alderman_dataset_id="htai-wnw4",
        library_dataset_id="x8fc-8rcq",
    )
    result = fetcher.fetch(cache_dir)
    assert result.status == "OK"
    assert result.record_count == 2
    aldr_geo = json.loads((cache_dir / "cdp_alderman_offices.geojson").read_text())
    assert aldr_geo["features"][0]["geometry"]["coordinates"] == [-87.628, 41.883]
