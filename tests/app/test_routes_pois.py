"""Tests for /pois route."""
from pathlib import Path

import pytest


@pytest.fixture
def pois_app(tiny_bikemap_db_with_pois: Path):
    from flask import Flask

    from app.core.poi_picker import load_pois
    from app.routes.pois import build_pois_blueprint

    app = Flask(__name__)
    app.register_blueprint(build_pois_blueprint(load_pois(tiny_bikemap_db_with_pois)))
    return app


def test_pois_post_returns_nearest_in_category(pois_app) -> None:
    client = pois_app.test_client()
    resp = client.post("/pois", json={
        "near": {"lat": 41.940, "lon": -87.670},
        "category": "school",
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["name"] == "Test Elementary"
    assert data["category"] == "school"
    assert "lat" in data and "lon" in data


def test_pois_post_returns_404_for_unknown_category(pois_app) -> None:
    client = pois_app.test_client()
    resp = client.post("/pois", json={
        "near": {"lat": 41.940, "lon": -87.670},
        "category": "nonexistent",
    })
    assert resp.status_code == 404


def test_pois_post_validates_payload(pois_app) -> None:
    client = pois_app.test_client()
    resp = client.post("/pois", json={"category": "school"})  # missing 'near'
    assert resp.status_code == 400


def test_pois_get_method_disallowed(pois_app) -> None:
    """Spec §3.8: coordinates never in URL query string. GET must be 405."""
    client = pois_app.test_client()
    resp = client.get("/pois?lat=41.94&lon=-87.67&category=school")
    assert resp.status_code == 405
