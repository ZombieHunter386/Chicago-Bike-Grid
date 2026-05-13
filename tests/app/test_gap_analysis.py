"""Tests for app.core.gap_analysis (D' corridor framing).

The spec §4.5 algorithm computes a CORRIDOR-level advocacy ask: the set of
LTS-above-tier segments and intersections on the fast route, scored as a
single combined hypothesis. Per-road marginal contributions identify which
streets are load-bearing.

See gap_analysis.py module docstring for the algorithm.
"""
from pathlib import Path

from app.core.gap_analysis import (
    CORRIDOR_SAVINGS_FLOOR_M,
    GapResult,
    analyze_gap,
)
from app.core.graph import load_graph, vertex_for_int_id


def test_gap_no_detour_returns_empty(divergent_bikemap_db: Path) -> None:
    """src == dst: trivial routes coincide, no corridor."""
    snap = load_graph(divergent_bikemap_db)
    v10 = vertex_for_int_id(snap, 10)
    assert v10 is not None
    res = analyze_gap(snap, v10, v10, "any")
    assert isinstance(res, GapResult)
    assert res.corridor is None
    assert res.intersections == ()


def test_gap_parent_tier_corridor_with_combined_savings(
    divergent_bikemap_db: Path,
) -> None:
    """At parent tier on v100→v400 (divergent fixture):
      fast = r3 (LTS-3 direct); safe = r1+r2 (LTS-1 detour, NOT fallback).
    Combined hypothesis upgrades r3 alone (only LTS>2 on fast). r3 weight
    drops from INF to len×1.2, beating the detour's len×1.0+len×1.0; safe
    flips to r3 → savings = safe_length - fast_length.
    flips_to_fully_safe=False (safe wasn't fallback to begin with).
    Corridor has exactly one road, marginal_loss == combined_savings."""
    snap = load_graph(divergent_bikemap_db)
    v100 = vertex_for_int_id(snap, 10)
    v400 = vertex_for_int_id(snap, 40)
    assert v100 is not None and v400 is not None
    res = analyze_gap(snap, v100, v400, "parent")
    assert res.safe_route_is_fallback is False
    assert res.corridor is not None
    expected = res.safe_route.length_m - res.fast_route.length_m
    assert abs(res.corridor.combined_savings_m - expected) < 1.0
    assert res.corridor.flips_to_fully_safe is False
    assert len(res.corridor.roads) == 1
    road = res.corridor.roads[0]
    assert road.name == "Test St 103"
    assert road.block_count == 1
    # Only road in the corridor → its marginal_loss equals combined_savings.
    assert abs(road.marginal_loss_m - res.corridor.combined_savings_m) < 1.0


def test_gap_corridor_overlay_geometry_is_multilinestring(
    divergent_bikemap_db: Path,
) -> None:
    """`fast_lts_overlay_wkt` is a MultiLineString of fast-route off-tier
    segments — the polyline overlay the frontend renders on the map."""
    snap = load_graph(divergent_bikemap_db)
    v100 = vertex_for_int_id(snap, 10)
    v400 = vertex_for_int_id(snap, 40)
    res = analyze_gap(snap, v100, v400, "parent")
    assert res.corridor is not None
    assert res.corridor.fast_lts_overlay_wkt.startswith("MULTILINESTRING")


def test_gap_road_geometry_is_multilinestring(
    divergent_bikemap_db: Path,
) -> None:
    """Per-road `geometry_wkt` is also a MultiLineString (one block here,
    but the shape is consistent so the frontend has one render path)."""
    snap = load_graph(divergent_bikemap_db)
    v100 = vertex_for_int_id(snap, 10)
    v400 = vertex_for_int_id(snap, 40)
    res = analyze_gap(snap, v100, v400, "parent")
    assert res.corridor is not None
    assert res.corridor.roads[0].geometry_wkt.startswith("MULTILINESTRING")


def test_gap_kid_tier_corridor_flips_fallback_to_in_tier(
    fallback_divergent_bikemap_db: Path,
) -> None:
    """fallback_divergent fixture at kid tier:
      fast = r3 (LTS-3, ~100m); safe = fallback r1+r2 (LTS-1+LTS-3, ~120m).
    Combined hypothesis upgrades r3 (only LTS>1 on fast — r2 isn't on fast):
      r3 weight 100×1.0=100, in-tier path now exists → safe flips to r3.
      savings ≈ 20m, flips_to_fully_safe = True.
    Threshold: 20m alone is below floor BUT flips=True surfaces the corridor."""
    snap = load_graph(fallback_divergent_bikemap_db)
    v10 = vertex_for_int_id(snap, 10)
    v40 = vertex_for_int_id(snap, 40)
    assert v10 is not None and v40 is not None
    res = analyze_gap(snap, v10, v40, "kid")
    assert res.safe_route_is_fallback is True
    assert res.corridor is not None, (
        "fallback safe + divergent fast must surface a corridor; flips_to_"
        "fully_safe lets the corridor surface even when savings < floor"
    )
    assert res.corridor.flips_to_fully_safe is True
    assert res.corridor.combined_savings_m < CORRIDOR_SAVINGS_FLOOR_M
    # The corridor's road is r3 (road_id=203, name 'Test St 203').
    assert len(res.corridor.roads) == 1
    assert res.corridor.roads[0].road_ids == (203,)
    assert res.corridor.roads[0].name == "Test St 203"


def test_gap_intersections_separated_from_corridor(
    divergent_bikemap_db: Path,
) -> None:
    """divergent fixture has no off-tier intersections (all lts_approach=1).
    The intersections list is therefore empty even though the corridor
    has segment members. Segments and intersections are separate groups."""
    snap = load_graph(divergent_bikemap_db)
    v100 = vertex_for_int_id(snap, 10)
    v400 = vertex_for_int_id(snap, 40)
    res = analyze_gap(snap, v100, v400, "parent")
    assert res.corridor is not None
    assert res.intersections == ()


def test_gap_empty_when_fast_equals_safe(tiny_bikemap_db: Path) -> None:
    """When fast.edge_path == safe.edge_path (no detour), corridor=None.
    Use src==dst as the simplest no-detour case."""
    snap = load_graph(tiny_bikemap_db)
    v100 = vertex_for_int_id(snap, 100)
    assert v100 is not None
    res = analyze_gap(snap, v100, v100, "kid")
    assert res.corridor is None
    assert res.intersections == ()


def test_gap_joint_segment_and_intersection_upgrade_flips_fallback(
    joint_upgrade_bikemap_db: Path,
) -> None:
    """Regression guard for the joint-upgrade bug. The fixture is constructed
    so the corridor flips fallback→in-tier ONLY when segments and intersections
    are upgraded together — segment-only or intersection-only leaves the
    relevant edge at INF because the un-upgraded component still drives the
    max-rule effective LTS to 3.

    Before the joint-pass fix, _apply_segment_upgrades and
    _apply_intersection_upgrades wrote sequentially to the weights array,
    each reading the OTHER component's pre-upgrade value via the max rule —
    so both ended up writing INF for the shared edge and flips_to_fully_safe
    stayed False. The combined helper now computes
    max(seg_eff, head_eff) where each component is tier_max_lts iff in the
    upgrade set.
    """
    snap = load_graph(joint_upgrade_bikemap_db)
    v10 = vertex_for_int_id(snap, 10)
    v30 = vertex_for_int_id(snap, 30)
    assert v10 is not None and v30 is not None
    res = analyze_gap(snap, v10, v30, "parent")
    assert res.safe_route_is_fallback is True, (
        "fixture expectation: every path from v10→v30 crosses an LTS-3 head "
        "intersection → safe falls back"
    )
    assert res.corridor is not None
    # The joint upgrade of r1 + v20 flips the route to in-tier — combined
    # savings > 0 AND flips_to_fully_safe is True. This is the assertion that
    # would fail under the pre-fix segment-pass-then-intersection-pass code.
    assert res.corridor.flips_to_fully_safe is True
    assert res.corridor.combined_savings_m > 0


def test_gap_roads_sorted_by_marginal_loss_descending(
    divergent_bikemap_db: Path,
) -> None:
    """Corridor.roads invariant: sorted by marginal_loss_m descending."""
    snap = load_graph(divergent_bikemap_db)
    v100 = vertex_for_int_id(snap, 10)
    v400 = vertex_for_int_id(snap, 40)
    res = analyze_gap(snap, v100, v400, "parent")
    assert res.corridor is not None
    losses = [r.marginal_loss_m for r in res.corridor.roads]
    assert losses == sorted(losses, reverse=True)
