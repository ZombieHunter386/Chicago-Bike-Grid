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


def test_route_edge_lts_reflects_street_stress_not_intersection(
    tiny_bikemap_db: Path,
) -> None:
    """edge_lts colors the route line by each STREET segment's own stress, not
    the stress of the intersection it leads into. Regression: a calm green block
    that merely approached a dangerous (lts_approach=3) intersection was painted
    red, so the danger looked smeared across the block instead of sitting at the
    crossing. The fast route v100->v400 passes THROUGH v300 (lts_approach=3) but
    both street segments are calm (seg_lts=1), so no edge may be colored 3.
    The intersection's danger is surfaced separately via vertex_lts."""
    snap = load_graph(tiny_bikemap_db)
    v100 = vertex_for_int_id(snap, 100)
    v300 = vertex_for_int_id(snap, 300)
    v400 = vertex_for_int_id(snap, 400)
    assert v100 is not None and v300 is not None and v400 is not None

    r = compute_fast_route(snap, v100, v400)
    assert r is not None
    assert v300 in r.vertex_path  # route really does cross the dangerous node
    assert 3 not in r.edge_lts    # ...but the calm street segments stay green
    assert r.edge_lts == [1, 1]
    # vertex_lts is the per-vertex intersection approach tier (length == vertex_path),
    # kept for reference. (Danger markers are now driven by vertex_cross_lts —
    # unsafe CROSS streets — not by this raw approach tier.)
    assert len(r.vertex_lts) == len(r.vertex_path)
    assert r.vertex_lts[r.vertex_path.index(v300)] == 3


def test_danger_marks_only_unsafe_cross_streets(tiny_bikemap_db: Path) -> None:
    """A node is a dangerous crossing ONLY when an unsafe (LTS-3) street the
    route does NOT ride meets it. The fast route v100->v500 rides the direct
    LTS-3 edge r5. At v500 the cross street r4 (v300<->v500, LTS-3) is unsafe,
    so v500 is flagged. At v100 the only cross street is r1 (LTS-1); the route's
    OWN r5 is LTS-3 but that's the line's color, not a node marker, so v100 is
    NOT flagged."""
    snap = load_graph(tiny_bikemap_db)
    v100 = vertex_for_int_id(snap, 100)
    v500 = vertex_for_int_id(snap, 500)
    assert v100 is not None and v500 is not None

    r = compute_fast_route(snap, v100, v500)
    assert r is not None
    assert r.vertex_path == [v100, v500]  # direct r5, no detour
    assert len(r.vertex_cross_lts) == len(r.vertex_path)
    # v500: unsafe cross street r4 → flagged.
    assert r.vertex_cross_lts[r.vertex_path.index(v500)] >= 3
    # v100: only calm cross street (r1, LTS-1); own r5 stress is on the line.
    assert r.vertex_cross_lts[r.vertex_path.index(v100)] < 3


def test_danger_not_marked_when_cross_streets_calm(tiny_bikemap_db: Path) -> None:
    """v300 has lts_approach=3, but on the v200->v500 route the route rides r3
    and r4 THROUGH v300, and the only streets crossing there (r1, r2) are calm
    (LTS-1). With the cross-street rule the node is NOT marked — there is no
    unsafe cross traffic to warn about, even though the intersection's approach
    tier is high."""
    snap = load_graph(tiny_bikemap_db)
    v200 = vertex_for_int_id(snap, 200)
    v300 = vertex_for_int_id(snap, 300)
    v500 = vertex_for_int_id(snap, 500)
    assert v200 is not None and v300 is not None and v500 is not None

    r = compute_fast_route(snap, v200, v500)
    assert r is not None
    assert v300 in r.vertex_path
    assert r.vertex_cross_lts[r.vertex_path.index(v300)] < 3


def test_route_carries_per_edge_lts(divergent_bikemap_db: Path) -> None:
    """edge_lts is the per-edge street-segment LTS the frontend uses to color the
    route polyline green (LTS 1) / orange (LTS 2) / red (LTS 3) per segment.
    Length must match edge_path. (Intersection lts_approach is 1 throughout this
    fixture, so the displayed segment LTS equals seg_lts.)"""
    snap = load_graph(divergent_bikemap_db)
    v10 = vertex_for_int_id(snap, 10)
    v40 = vertex_for_int_id(snap, 40)
    assert v10 is not None and v40 is not None

    # Fast route uses r3 (direct LTS-3 edge); edge_lts should be [3].
    fast = compute_fast_route(snap, v10, v40)
    assert fast is not None
    assert len(fast.edge_lts) == len(fast.edge_path)
    assert fast.edge_lts == [3]

    # Safe route at parent tier uses r1 + r2 (LTS-1 detour); edge_lts == [1, 1].
    # (Intersection lts_approach is 1 throughout this fixture, so eff == seg_lts.)
    safe = compute_safe_route(snap, v10, v40, "parent")
    assert safe is not None
    assert len(safe.edge_lts) == len(safe.edge_path)
    assert safe.edge_lts == [1, 1]


def test_trivial_route_has_empty_edge_lts(tiny_bikemap_db: Path) -> None:
    """src == dst yields zero-length route; edge_lts must be the empty list
    so frontend split-by-LTS rendering doesn't try to index into nothing."""
    snap = load_graph(tiny_bikemap_db)
    v100 = vertex_for_int_id(snap, 100)
    assert v100 is not None
    r = compute_fast_route(snap, v100, v100)
    assert r is not None
    assert r.edge_lts == []


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
