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


def test_routes_payload_surfaces_dangerous_intersections(routes_app) -> None:
    """A route crossing a dangerous intersection (lts_approach=3) reports it as a
    point in `danger_intersections` so the frontend can mark the crossing — while
    the calm street segments leading into it stay green (not reddened). The fast
    route v100->v400 passes through v300 (lts_approach=3) on calm (lts=1) streets."""
    client = routes_app.test_client()
    resp = client.post("/routes", json={
        "home": {"lat": 41.940, "lon": -87.680},  # near v100
        "dest": {"lat": 41.940, "lon": -87.670},  # near v400 (path crosses v300)
        "tier": "any",
    })
    assert resp.status_code == 200
    fast = resp.get_json()["fast"]
    assert "danger_intersections" in fast
    di = fast["danger_intersections"]
    assert len(di) >= 1
    for d in di:
        assert d["lts"] == 3
        assert isinstance(d["lat"], float) and isinstance(d["lon"], float)
    # the calm street segments themselves are NOT painted red
    assert 3 not in fast["polyline_lts"]


def test_routes_danger_excludes_calm_crossing_of_high_approach_node(routes_app) -> None:
    """A route that passes through a high-approach-tier node on calm cross
    streets must NOT report it as a danger intersection. v300 has lts_approach=3,
    but the v200->v500 route rides r3/r4 through it while the only crossing
    streets (r1, r2) are calm (LTS-1) — so no marker is emitted there."""
    client = routes_app.test_client()
    resp = client.post("/routes", json={
        "home": {"lat": 41.945, "lon": -87.675},  # near v200
        "dest": {"lat": 41.935, "lon": -87.675},  # near v500 (path crosses v300)
        "tier": "any",
    })
    assert resp.status_code == 200
    fast = resp.get_json()["fast"]
    v300 = {"lat": 41.940, "lon": -87.675}
    for d in fast["danger_intersections"]:
        assert not (abs(d["lat"] - v300["lat"]) < 1e-6
                    and abs(d["lon"] - v300["lon"]) < 1e-6), (
            "v300 has calm cross streets on this route and must not be flagged"
        )


def test_routes_payload_carries_polyline_lts_matching_segment_count(routes_app) -> None:
    """Each route's polyline_lts holds one LTS value per polyline SEGMENT,
    so polyline_lts.length == polyline.length - 1. The frontend uses this to
    split the safe-route LineString into one Feature per contiguous same-LTS
    run, coloring green / orange / red per segment."""
    client = routes_app.test_client()
    resp = client.post("/routes", json={
        "home": {"lat": 41.940, "lon": -87.680},
        "dest": {"lat": 41.940, "lon": -87.670},
        "tier": "any",
    })
    assert resp.status_code == 200
    data = resp.get_json()
    for kind in ("fast", "safe"):
        route = data[kind]
        assert "polyline_lts" in route, f"{kind} missing polyline_lts"
        polyline = route["polyline"]
        polyline_lts = route["polyline_lts"]
        # N-vertex polyline has N-1 segments; LTS list length must match.
        assert len(polyline_lts) == max(0, len(polyline) - 1), (
            f"{kind}: polyline_lts has {len(polyline_lts)} entries but "
            f"polyline has {len(polyline)} vertices (expected {len(polyline) - 1})"
        )
        # Every LTS value must be 1, 2, or 3 (the only legal LTS levels).
        for v in polyline_lts:
            assert v in (1, 2, 3), f"{kind}: unexpected LTS value {v}"
