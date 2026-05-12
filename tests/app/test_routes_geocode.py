"""Tests for /geocode proxy."""
from unittest.mock import patch


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
