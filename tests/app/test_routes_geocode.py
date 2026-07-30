"""Tests for /geocode proxy."""
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _make_app():
    from flask import Flask

    from app.routes.geocode import build_geocode_blueprint

    app = Flask(__name__)
    app.register_blueprint(build_geocode_blueprint(user_agent="test/1.0"))
    return app


def test_geocode_proxies_to_nominatim_and_returns_first_result() -> None:
    app = _make_app()
    fake_response = [{
        "display_name": "1234 W Foster Ave, Chicago, IL, USA",
        "lat": "41.9755",
        "lon": "-87.6890",
    }]
    with patch("app.routes.geocode._fetch_nominatim", return_value=fake_response):
        client = app.test_client()
        resp = client.post("/geocode", json={"address": "1234 W Foster Ave"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["lat"] == 41.9755
        assert data["lon"] == -87.6890
        assert "Foster" in data["display_name"]


def test_geocode_returns_404_when_no_results() -> None:
    app = _make_app()
    with patch("app.routes.geocode._fetch_nominatim", return_value=[]):
        client = app.test_client()
        resp = client.post("/geocode", json={"address": "blank"})
        assert resp.status_code == 404


def test_geocode_400_on_missing_address() -> None:
    app = _make_app()
    client = app.test_client()
    resp = client.post("/geocode", json={})
    assert resp.status_code == 400


def test_geocode_suggest_returns_multiple_results() -> None:
    """The /geocode/suggest endpoint returns up to 5 Nominatim matches for
    type-ahead autocomplete. Each row carries display_name, lat, lon, and
    place_id; the frontend uses lat/lon to set state.home on click and
    display_name as the dropdown label."""
    app = _make_app()
    fake = [
        {"display_name": "111 Foster Ave", "lat": "41.97", "lon": "-87.68", "place_id": 1},
        {"display_name": "222 Foster Ave", "lat": "41.98", "lon": "-87.69", "place_id": 2},
        {"display_name": "333 Foster Ave", "lat": "41.99", "lon": "-87.70", "place_id": 3},
    ]
    with patch("app.routes.geocode._fetch_nominatim", return_value=fake):
        client = app.test_client()
        resp = client.post("/geocode/suggest", json={"address": "Foster Ave"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["results"]) == 3
        assert data["results"][0]["lat"] == 41.97
        assert data["results"][0]["place_id"] == 1


def test_geocode_suggest_short_query_returns_empty_without_calling_nominatim() -> None:
    """Short queries (<3 chars) skip Nominatim entirely to avoid burning the
    1s global throttle on input that's too short to disambiguate anything."""
    app = _make_app()
    with patch("app.routes.geocode._fetch_nominatim") as mock_fetch:
        client = app.test_client()
        resp = client.post("/geocode/suggest", json={"address": "Fo"})
        assert resp.status_code == 200
        assert resp.get_json() == {"results": []}
        mock_fetch.assert_not_called()


def test_fetch_nominatim_bounds_results_to_service_area() -> None:
    """Prefilled suggestions must stay inside the service area: the user
    reported the search bar offering addresses from all over the US.
    `_fetch_nominatim` must pass a `viewbox` and `bounded=1` so Nominatim
    restricts results to the service-area bounding box, not the whole country.

    Bounds widened from the Chicago-only box to all of Cook County
    (2026-07-30). The exact edges are pinned against sources.yaml by
    `test_geocode_viewbox_matches_target_bbox`; this test only sanity-checks
    that the values are plausible lat/lons in the right corner of the map.
    """
    from app.routes import geocode

    mock_resp = MagicMock()
    mock_resp.json.return_value = []
    mock_resp.raise_for_status.return_value = None
    with patch("app.routes.geocode.requests.get", return_value=mock_resp) as mock_get:
        geocode._fetch_nominatim("Foster Ave", "test/1.0", limit=5)

    params = mock_get.call_args.kwargs["params"]
    assert params["bounded"] == "1"
    assert "viewbox" in params
    # viewbox is "left,top,right,bottom" — four comma-separated floats covering
    # Cook County. Sanity-check the longitudes/latitudes are in range.
    left, top, right, bottom = (float(x) for x in params["viewbox"].split(","))
    assert -88.3 < left < -88.2 and -87.6 < right < -87.5
    assert 41.4 < bottom < 41.5 and 42.1 < top < 42.2
    assert left < right and bottom < top


def test_geocode_suggest_skips_malformed_nominatim_rows() -> None:
    """If Nominatim returns a row missing lat/lon (or with non-numeric
    values), skip it rather than 500ing the whole response."""
    app = _make_app()
    fake = [
        {"display_name": "Good", "lat": "41.97", "lon": "-87.68"},
        {"display_name": "Bad: no lat"},
        {"display_name": "Bad: bogus lat", "lat": "not-a-number", "lon": "0"},
    ]
    with patch("app.routes.geocode._fetch_nominatim", return_value=fake):
        client = app.test_client()
        resp = client.post("/geocode/suggest", json={"address": "Foster"})
        assert resp.status_code == 200
        results = resp.get_json()["results"]
        assert len(results) == 1
        assert results[0]["display_name"] == "Good"


def test_geocode_viewbox_matches_target_bbox() -> None:
    """The Nominatim viewbox must cover exactly the prep target bbox.

    geocode.py sends `bounded=1`, so this box hard-limits which addresses a
    user can even enter, while `target.bbox` in sources.yaml decides which
    streets exist in the routing graph. If the viewbox is narrower, valid
    addresses in the service area silently return "no results" — which is
    what happened to every suburban address when the 2026-07-30 Cook County
    expansion widened the graph but left this constant on the old Chicago box.
    If it is wider, users can pick addresses the router has no streets for.
    """
    import yaml

    from app.routes.geocode import _SERVICE_AREA_VIEWBOX

    repo_root = Path(__file__).resolve().parents[2]
    cfg = yaml.safe_load((repo_root / "prep" / "config" / "sources.yaml").read_text())
    bbox = cfg["target"]["bbox"]

    # Nominatim viewbox order is left,top,right,bottom.
    left, top, right, bottom = (float(v) for v in _SERVICE_AREA_VIEWBOX.split(","))
    assert left == pytest.approx(bbox["min_lng"]), "viewbox west edge != target bbox"
    assert right == pytest.approx(bbox["max_lng"]), "viewbox east edge != target bbox"
    assert top == pytest.approx(bbox["max_lat"]), "viewbox north edge != target bbox"
    assert bottom == pytest.approx(bbox["min_lat"]), "viewbox south edge != target bbox"
