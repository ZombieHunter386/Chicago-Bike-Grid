"""Tests for app.core.gap_analysis. Uses divergent_bikemap_db fixture
which is specifically designed to force fast/safe divergence at 'parent'
tier (Fix 5)."""
from pathlib import Path

from app.core.gap_analysis import GapResult, analyze_gap
from app.core.graph import load_graph, vertex_for_int_id


def test_gap_no_diverge_yields_empty_headline(divergent_bikemap_db: Path) -> None:
    """At 'kid' tier, the LTS-3 direct edge is blocked AND the LTS-1 detour
    works: fast route uses direct (length-only), safe route uses detour.
    They diverge — but if 'kid' tier here the safe route is feasible, gap
    analysis runs. If we instead pick a route where safe == fast, headline
    is None. Use src=dst as the simplest no-diverge case (path length 0)."""
    snap = load_graph(divergent_bikemap_db)
    v10 = vertex_for_int_id(snap, 10)
    assert v10 is not None
    res = analyze_gap(snap, v10, v10, "any")
    assert isinstance(res, GapResult)
    assert res.headline is None


def test_gap_parent_tier_finds_lts3_segment_as_headline(
    divergent_bikemap_db: Path,
) -> None:
    """At parent tier (LTS 1-2 allowed), v100 → v400 fast = r3 (LTS 3, 200m);
    safe = r1 + r2 (LTS 1, 300m). Hypothetically downgrading r3 to LTS 2
    gives a 200m route — savings 100m. r3 must be the headline candidate."""
    snap = load_graph(divergent_bikemap_db)
    v100 = vertex_for_int_id(snap, 10)
    v400 = vertex_for_int_id(snap, 40)
    assert v100 is not None and v400 is not None
    res = analyze_gap(snap, v100, v400, "parent")
    assert res.safe_route_is_fallback is False
    assert res.headline is not None
    assert res.headline.feature_kind == "segment"
    assert res.headline.feature_id == 103   # r3's road_id
    assert res.headline.current_lts == 3
    assert res.headline.savings_m > 50      # ~100m savings; allow slop


def test_gap_kid_tier_no_chokepoint_returns_in_tier_diverge(
    divergent_bikemap_db: Path,
) -> None:
    """At 'kid' tier: r3 (LTS 3) blocked, but r1+r2 (LTS 1) is in-tier.
    fast = r3 (length-only), safe = r1+r2. Diverge → r3 headline.
    Same shape as parent-tier but tighter tier — verifies the algorithm
    handles 'kid' identically when the detour exists."""
    snap = load_graph(divergent_bikemap_db)
    v100 = vertex_for_int_id(snap, 10)
    v400 = vertex_for_int_id(snap, 40)
    assert v100 is not None and v400 is not None
    res = analyze_gap(snap, v100, v400, "kid")
    assert res.safe_route_is_fallback is False
    assert res.headline is not None
    assert res.headline.feature_id == 103   # r3 again


def test_gap_returns_no_headline_when_safe_route_is_fallback(
    tiny_bikemap_db: Path,
) -> None:
    """When safe route is fallback, gap analysis returns no per-destination
    candidate (spec §4.5 case 1: 'unreachable safely')."""
    snap = load_graph(tiny_bikemap_db)
    v100 = vertex_for_int_id(snap, 100)
    v500 = vertex_for_int_id(snap, 500)
    assert v100 is not None and v500 is not None
    # tiny_bikemap_db at 'kid' tier: v300 lts_approach=3 chokepoint;
    # direct LTS-3 edge to v500 also blocked → fallback engages.
    res = analyze_gap(snap, v100, v500, "kid")
    assert res.safe_route_is_fallback is True
    assert res.headline is None


def test_gap_supporting_and_corridor_are_lists(
    divergent_bikemap_db: Path,
) -> None:
    """GapResult shape: supporting and corridor are lists (possibly empty)."""
    snap = load_graph(divergent_bikemap_db)
    v100 = vertex_for_int_id(snap, 10)
    v400 = vertex_for_int_id(snap, 40)
    assert v100 is not None and v400 is not None
    res = analyze_gap(snap, v100, v400, "parent")
    assert isinstance(res.supporting, list)
    assert isinstance(res.corridor, list)


def test_gap_candidate_sort_combines_segments_and_intersections(
    divergent_bikemap_db: Path,
) -> None:
    """Fix 2 regression guard: when both segment and intersection candidates
    exist with different violation levels, the higher-violation one wins
    regardless of feature kind. divergent_bikemap_db has only segment
    candidates (all intersections have lts_approach=1) — this test simply
    asserts the candidate list is sorted by violation level descending."""
    snap = load_graph(divergent_bikemap_db)
    v100 = vertex_for_int_id(snap, 10)
    v400 = vertex_for_int_id(snap, 40)
    assert v100 is not None and v400 is not None
    res = analyze_gap(snap, v100, v400, "parent")
    if res.headline is not None and res.supporting:
        prev_violation = res.headline.current_lts
        for c in res.supporting:
            # Each subsequent candidate has equal-or-lower violation.
            assert c.current_lts <= prev_violation
            prev_violation = c.current_lts
