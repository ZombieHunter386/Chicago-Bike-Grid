"""Tests for app.core.routing — fast, safe, and fallback shortest paths."""
from pathlib import Path

from app.core.graph import load_graph, vertex_for_int_id
from app.core.routing import (
    Route,
    compute_fast_route,
    compute_safe_route,
)


def test_fast_route_minimizes_distance(tiny_bikemap_db: Path) -> None:
    """Fast route v100 → v400 should pass through v300 (the only direct path)."""
    snap = load_graph(tiny_bikemap_db)
    v100 = vertex_for_int_id(snap, 100)
    v400 = vertex_for_int_id(snap, 400)
    assert v100 is not None and v400 is not None
    r = compute_fast_route(snap, v100, v400)
    assert r is not None
    assert isinstance(r, Route)
    assert r.vertex_path[0] == v100
    assert r.vertex_path[-1] == v400
    assert vertex_for_int_id(snap, 300) in r.vertex_path
    assert r.is_fallback is False
    assert r.length_m > 0


def test_safe_route_kid_tier_avoids_lts3(tiny_bikemap_db: Path) -> None:
    """v100 → v500: direct edge (road 5, LTS 3) is blocked at kid tier;
    must detour via v300, but v300 has lts_approach=3 — every entering edge
    is effectively LTS 3 → no in-tier path → fallback engages."""
    snap = load_graph(tiny_bikemap_db)
    v100 = vertex_for_int_id(snap, 100)
    v500 = vertex_for_int_id(snap, 500)
    assert v100 is not None and v500 is not None
    r = compute_safe_route(snap, v100, v500, "kid")
    assert r is not None
    # Fallback expected because LTS-3 chokepoint at v300 + LTS-3 direct edge.
    assert r.is_fallback is True


def test_safe_route_any_tier_uses_shortest_lts3_allowed(tiny_bikemap_db: Path) -> None:
    """At 'any' tier (LTS 1-3 allowed with 1.5× penalty), v100 → v500 should
    use the direct LTS-3 edge (road 5) since the detour through v300 also hits LTS 3."""
    snap = load_graph(tiny_bikemap_db)
    v100 = vertex_for_int_id(snap, 100)
    v500 = vertex_for_int_id(snap, 500)
    assert v100 is not None and v500 is not None
    r = compute_safe_route(snap, v100, v500, "any")
    assert r is not None
    assert r.is_fallback is False
    # Direct path = 2 vertices (v100, v500); detour via v300 = 3 vertices.
    assert len(r.vertex_path) == 2


def test_safe_route_records_lts_distribution(tiny_bikemap_db: Path) -> None:
    snap = load_graph(tiny_bikemap_db)
    v100 = vertex_for_int_id(snap, 100)
    v400 = vertex_for_int_id(snap, 400)
    assert v100 is not None and v400 is not None
    r = compute_safe_route(snap, v100, v400, "any")
    assert r is not None
    assert sum(r.lts_distribution.values()) == len(r.edge_path)


def test_compute_routes_return_trivial_route_when_src_equals_dst(
    tiny_bikemap_db: Path,
) -> None:
    """Fix F: src == dst returns a Route with zero length and empty edge_path."""
    snap = load_graph(tiny_bikemap_db)
    v100 = vertex_for_int_id(snap, 100)
    assert v100 is not None
    fast = compute_fast_route(snap, v100, v100)
    assert fast is not None
    assert fast.length_m == 0.0
    assert fast.edge_path == []
    assert fast.vertex_path == [v100]
    safe = compute_safe_route(snap, v100, v100, "any")
    assert safe is not None
    assert safe.length_m == 0.0
    assert safe.is_fallback is False


def test_route_returns_none_for_unreachable_endpoints() -> None:
    """An isolated vertex should return None even at fallback weights."""
    # Build a 2-component graph manually.
    import tempfile
    from pathlib import Path as _Path

    from prep.db.builder import DbBuilder
    from prep.lts.ingest import IntersectionRecord, SegmentRecord

    with tempfile.TemporaryDirectory() as tmp:
        db_path = _Path(tmp) / "isolated.db"
        b = DbBuilder(db_path)
        b.create_schema()
        b.insert_intersections([
            IntersectionRecord(osm_id=1, lts_approach=1, signalized=None,
                               lanes_crossed=None, geometry_wkt="POINT(-87.7 41.9)",
                               raw_properties={}),
            IntersectionRecord(osm_id=2, lts_approach=1, signalized=None,
                               lanes_crossed=None, geometry_wkt="POINT(-87.6 41.9)",
                               raw_properties={}),
            IntersectionRecord(osm_id=3, lts_approach=1, signalized=None,
                               lanes_crossed=None, geometry_wkt="POINT(-87.5 41.9)",
                               raw_properties={}),
        ])
        b.insert_streets([
            SegmentRecord(road_id=1, osm_id=10, head_int_id=1, tail_int_id=2,
                          name=None, lts=1, highway="residential", speed=25,
                          ft_int_str=1, tf_int_str=1,
                          geometry_wkt="LINESTRING(-87.7 41.9, -87.6 41.9)",
                          raw_properties={}),
            # v3 has no edge — isolated vertex.
        ])
        b.record_schema_meta(code_version="test")
        b.close()

        snap = load_graph(db_path)
        v1 = vertex_for_int_id(snap, 1)
        v3 = vertex_for_int_id(snap, 3)
        assert v1 is not None and v3 is not None
        assert compute_fast_route(snap, v1, v3) is None
        assert compute_safe_route(snap, v1, v3, "kid") is None
