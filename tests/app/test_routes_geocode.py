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
