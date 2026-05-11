"""Tests for the graph-snapshot loader (app.core.graph)."""
from pathlib import Path

from app.core.graph import (
    GraphSnapshot,
    edges_for_road_id,
    load_graph,
    nearest_vertex,
    vertex_for_int_id,
)


def test_load_graph_creates_directed_graph(tiny_bikemap_db: Path) -> None:
    snap = load_graph(tiny_bikemap_db)
    assert isinstance(snap, GraphSnapshot)
    # 5 intersections → 5 vertices.
    assert snap.g.vcount() == 5
    # 5 streets × 2 directions → 10 directed edges.
    assert snap.g.ecount() == 10
    assert snap.g.is_directed()


def test_load_graph_maps_int_id_to_vertex_idx(tiny_bikemap_db: Path) -> None:
    snap = load_graph(tiny_bikemap_db)
    # vertex_for_int_id (sorted-array helper) replaces the prior dict.
    for int_id in (100, 200, 300, 400, 500):
        assert vertex_for_int_id(snap, int_id) is not None
    # vertex_to_int_id is the inverse map (Fix 12).
    for int_id in (100, 200, 300, 400, 500):
        vidx = vertex_for_int_id(snap, int_id)
        assert vidx is not None
        assert int(snap.vertex_to_int_id[vidx]) == int_id
    # Missing keys must return None (regression guard for the searchsorted
    # equality check that prevents missing keys from mapping to neighbours).
    assert vertex_for_int_id(snap, 99999) is None


def test_load_graph_effective_lts_applies_max_rule(tiny_bikemap_db: Path) -> None:
    """An edge entering v300 (lts_approach=3) should have effective_lts=3
    even when its segment_lts=1. (Spec §4.1 max rule.)"""
    snap = load_graph(tiny_bikemap_db)
    v100 = vertex_for_int_id(snap, 100)
    v300 = vertex_for_int_id(snap, 300)
    assert v100 is not None and v300 is not None
    eid = snap.g.get_eid(v100, v300)
    assert snap.edge_seg_lts[eid] == 1
    assert snap.edge_head_lts[eid] == 3


def test_load_graph_reverse_edge_uses_reverse_head_lts(tiny_bikemap_db: Path) -> None:
    """The reverse edge v300 → v100 enters v100 (lts_approach=1)."""
    snap = load_graph(tiny_bikemap_db)
    v100 = vertex_for_int_id(snap, 100)
    v300 = vertex_for_int_id(snap, 300)
    assert v100 is not None and v300 is not None
    eid_rev = snap.g.get_eid(v300, v100)
    assert snap.edge_seg_lts[eid_rev] == 1
    assert snap.edge_head_lts[eid_rev] == 1


def test_load_graph_precomputes_main_and_fallback_per_tier_weights(
    tiny_bikemap_db: Path,
) -> None:
    """Both main and fallback weights are precomputed at load (Fix 9)."""
    snap = load_graph(tiny_bikemap_db)
    assert set(snap.base_weights_by_tier.keys()) == {"kid", "parent", "any"}
    assert set(snap.fallback_weights_by_tier.keys()) == {"kid", "parent", "any"}
    for tier_weights in snap.base_weights_by_tier.values():
        assert len(tier_weights) == snap.g.ecount()
    for tier_weights in snap.fallback_weights_by_tier.values():
        assert len(tier_weights) == snap.g.ecount()


def test_load_graph_populates_in_memory_road_metadata(tiny_bikemap_db: Path) -> None:
    """Per-road_id arrays are loaded for in-memory gap-analysis filtering (Fix 3)."""
    snap = load_graph(tiny_bikemap_db)
    # 5 unique road_ids in the fixture.
    assert len(snap.road_id_array) == 5
    # edges_for_road_id (searchsorted helper) replaces the prior road_id_to_idx
    # dict; it returns the (forward, reverse) edge id pair.
    for road_id in (1, 2, 3, 4, 5):
        edge_pair = edges_for_road_id(snap, road_id)
        assert edge_pair is not None
        forward_eid, reverse_eid = edge_pair
        assert reverse_eid == forward_eid + 1
        # Both edges in the pair must point at the queried road.
        assert int(snap.edge_road_id[forward_eid]) == road_id
        assert int(snap.edge_road_id[reverse_eid]) == road_id
    # Missing road id returns None (searchsorted equality check guard).
    assert edges_for_road_id(snap, 99999) is None
    # road_lts_array, road_length_array, road_bbox_proj are aligned.
    assert len(snap.road_lts_array) == 5
    assert len(snap.road_length_array) == 5
    assert snap.road_bbox_proj.shape == (5, 4)
    # Bbox bounds are sane: minx <= maxx, miny <= maxy.
    for i in range(5):
        minx, miny, maxx, maxy = snap.road_bbox_proj[i]
        assert minx <= maxx and miny <= maxy


def test_load_graph_populates_per_vertex_metadata(tiny_bikemap_db: Path) -> None:
    """Per-vertex arrays for gap-analysis intersection candidates (Fix 3)."""
    snap = load_graph(tiny_bikemap_db)
    assert len(snap.vertex_lts_approach) == snap.g.vcount()
    assert len(snap.vertex_on_hin) == snap.g.vcount()
    # v300 has lts_approach=3 in the fixture.
    v300 = vertex_for_int_id(snap, 300)
    assert v300 is not None
    assert snap.vertex_lts_approach[v300] == 3


def test_nearest_vertex_returns_idx_and_distance(tiny_bikemap_db: Path) -> None:
    snap = load_graph(tiny_bikemap_db)
    # Query at v300's coordinates exactly — distance ≈ 0.
    v_idx, dist_m = nearest_vertex(snap, 41.940, -87.675)
    assert v_idx == vertex_for_int_id(snap, 300)
    assert dist_m < 1.0  # within 1 metre


def test_nearest_vertex_distance_increases_with_offset(tiny_bikemap_db: Path) -> None:
    """Distance is in EPSG:6454 metres (Fix 8)."""
    snap = load_graph(tiny_bikemap_db)
    # ~100m offset NE of v100.
    _, dist = nearest_vertex(snap, 41.9402, -87.6798)
    assert 5.0 < dist < 200.0  # between 5m and 200m sanity range
