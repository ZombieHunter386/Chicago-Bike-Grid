"""Tests for /routes route."""
from pathlib import Path

import pytest


@pytest.fixture
def routes_app(tiny_bikemap_db: Path):
    from flask import Flask

    from app.core.graph import load_graph
    from app.routes.routing import build_routes_blueprint

    app = Flask(__name__)
    app.register_blueprint(build_routes_blueprint(load_graph(tiny_bikemap_db)))
    return app


def test_routes_returns_fast_and_safe_for_any_tier(routes_app) -> None:
    client = routes_app.test_client()
    resp = client.post("/routes", json={
        "home": {"lat": 41.940, "lon": -87.680},  # near v100
        "dest": {"lat": 41.940, "lon": -87.670},  # near v400
        "tier": "any",
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert "fast" in data
    assert "safe" in data
    assert data["fast"]["length_m"] > 0
    assert isinstance(data["fast"]["polyline"], list)
    assert isinstance(data["safe"]["polyline"], list)
    assert data["safe"]["is_fallback"] is False


def test_routes_flags_fallback_at_kid_tier_when_blocked(routes_app) -> None:
    client = routes_app.test_client()
    # v100 → v500 at kid tier — known blocked (LTS-3 chokepoint).
    resp = client.post("/routes", json={
        "home": {"lat": 41.940, "lon": -87.680},
        "dest": {"lat": 41.935, "lon": -87.675},
        "tier": "kid",
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["safe"]["is_fallback"] is True


def test_routes_400_on_invalid_tier(routes_app) -> None:
    client = routes_app.test_client()
    resp = client.post("/routes", json={
        "home": {"lat": 41.94, "lon": -87.68},
        "dest": {"lat": 41.94, "lon": -87.67},
        "tier": "BOGUS",
    })
    assert resp.status_code == 400


def test_routes_400_when_home_far_outside_graph_extent(routes_app) -> None:
    """Fix 8: home in Wisconsin (>5km from any synthetic vertex) → 400."""
    client = routes_app.test_client()
    resp = client.post("/routes", json={
        "home": {"lat": 43.0, "lon": -89.0},   # Madison, WI
        "dest": {"lat": 41.94, "lon": -87.67},
        "tier": "any",
    })
    assert resp.status_code == 400
    assert "outside" in resp.get_json()["error"].lower() or \
           "too far" in resp.get_json()["error"].lower()


def test_routes_get_method_disallowed(routes_app) -> None:
    client = routes_app.test_client()
    resp = client.get("/routes?home_lat=41.94&home_lon=-87.68&dest_lat=41.94&dest_lon=-87.67&tier=any")
    assert resp.status_code == 405
