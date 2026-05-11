# tests/prep/test_speed_limits_fetcher.py
import json
from pathlib import Path

import responses

from prep.fetchers.speed_limits import SpeedLimitsFetcher


@responses.activate
def test_speed_limits_fetcher_writes_geojson(
    cache_dir: Path,
    fixtures_dir: Path,
) -> None:
    payload = json.loads((fixtures_dir / "speed_limits_response.json").read_text())
    responses.add(
        responses.GET,
        "https://data.cityofchicago.org/resource/spqx-js37.json",
        json=payload,
        status=200,
    )

    fetcher = SpeedLimitsFetcher(domain="data.cityofchicago.org", dataset_id="spqx-js37")
    result = fetcher.fetch(cache_dir)

    assert result.status == "OK"
    assert result.record_count == 2
    out = cache_dir / "chicago_speed_limits.geojson"
    geo = json.loads(out.read_text())
    assert geo["features"][0]["properties"]["speed_limit"] == "30"
