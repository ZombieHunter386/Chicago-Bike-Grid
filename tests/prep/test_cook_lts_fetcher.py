import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from prep.fetchers.cook_lts import (
    MIN_EXPECTED_RECORDS,
    SNAPSHOT_FILENAME,
    CookLtsFetcher,
    parse_cook_lts,
)

LAYER_URL = "https://example.com/DOTH_expanded/MapServer/14"


def _page(features: list[dict], exceeded: bool) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "features": [{"attributes": a} for a in features],
        "exceededTransferLimit": exceeded,
    }
    return resp


@patch("prep.fetchers.cook_lts.requests.get")
def test_fetch_paginates_until_transfer_limit_clears(mock_get, tmp_path: Path) -> None:
    page1 = [{"way_id": float(i), "lts": "1"} for i in range(2000)]
    page2 = [{"way_id": 999001.0, "lts": "4"}]
    mock_get.side_effect = [_page(page1, True), _page(page2, False)]

    result = CookLtsFetcher(layer_url=LAYER_URL).fetch(tmp_path)

    assert result.record_count == 2001
    assert mock_get.call_count == 2
    # Second call must advance resultOffset past page 1.
    second_params = mock_get.call_args_list[1].kwargs["params"]
    assert second_params["resultOffset"] == 2000
    assert second_params["returnGeometry"] == "false"
    saved = json.loads((tmp_path / SNAPSHOT_FILENAME).read_text())
    assert len(saved) == 2001


@patch("prep.fetchers.cook_lts.requests.get")
def test_fetch_warns_when_record_count_suspiciously_low(mock_get, tmp_path: Path) -> None:
    mock_get.return_value = _page([{"way_id": 1.0, "lts": "1"}], False)
    result = CookLtsFetcher(layer_url=LAYER_URL).fetch(tmp_path)
    assert result.status == "WARN"
    assert result.record_count == 1
    assert any(str(MIN_EXPECTED_RECORDS) in w for w in result.warnings)


@patch("prep.fetchers.cook_lts.requests.get")
def test_fetch_fails_on_http_error(mock_get, tmp_path: Path) -> None:
    resp = MagicMock()
    resp.status_code = 503
    mock_get.return_value = resp
    result = CookLtsFetcher(layer_url=LAYER_URL).fetch(tmp_path)
    assert result.status == "FAIL"


@patch("prep.fetchers.cook_lts.requests.get")
def test_fetch_fails_on_arcgis_error_payload(mock_get, tmp_path: Path) -> None:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"error": {"code": 400, "message": "bad"}}
    mock_get.return_value = resp
    result = CookLtsFetcher(layer_url=LAYER_URL).fetch(tmp_path)
    assert result.status == "FAIL"


def test_parse_builds_way_lts_map_worst_wins(tmp_path: Path) -> None:
    snapshot = tmp_path / SNAPSHOT_FILENAME
    snapshot.write_text(json.dumps([
        {"way_id": 24072568.0, "lts": "1"},
        {"way_id": 24072568.0, "lts": "3"},   # duplicate way -> worst (3) wins
        {"way_id": 354396977.0, "lts": "4"},
        {"way_id": 111.0, "lts": "garbage"},  # unparseable -> skipped
        {"way_id": 112.0, "lts": "7"},        # out of range -> skipped
        {"way_id": None, "lts": "2"},         # no way id -> skipped
    ]))

    way_lts = parse_cook_lts(snapshot)

    # esri doubles become plain int-strings to match OsmEdge.osm_way_ids.
    assert way_lts == {"24072568": 3, "354396977": 4}
