"""Tier weight config sanity tests — single source from spec §0.1."""
from app.core.weights import (
    INF_WEIGHT,
    TIERS,
    fallback_weight_for,
    main_weight_for,
)


def test_three_tiers_defined() -> None:
    assert set(TIERS.keys()) == {"kid", "parent", "any"}


def test_kid_tier_blocks_lts2_and_lts3() -> None:
    assert main_weight_for("kid", 1) == 1.0
    assert main_weight_for("kid", 2) == INF_WEIGHT
    assert main_weight_for("kid", 3) == INF_WEIGHT


def test_parent_tier_allows_lts2_blocks_lts3() -> None:
    assert main_weight_for("parent", 1) == 1.0
    assert main_weight_for("parent", 2) == 1.2
    assert main_weight_for("parent", 3) == INF_WEIGHT


def test_any_tier_allows_all() -> None:
    assert main_weight_for("any", 1) == 1.0
    assert main_weight_for("any", 2) == 1.2
    assert main_weight_for("any", 3) == 1.5


def test_kid_fallback_strongly_penalizes_higher_lts() -> None:
    assert fallback_weight_for("kid", 1) == 1.0
    assert fallback_weight_for("kid", 2) == 5.0
    assert fallback_weight_for("kid", 3) == 20.0


def test_inf_weight_dominates_any_realistic_path_cost() -> None:
    """Routing detects 'no in-tier path' by checking whether any edge in the
    Dijkstra result has weight >= INF_WEIGHT. Sanity: INF_WEIGHT must dwarf
    any plausible weighted cost from an all-allowed path. Chicago's diameter
    is ~50 km × 1.5 max weight = 75 km of weighted cost; INF_WEIGHT=1e9 is
    13 orders of magnitude above that."""
    assert INF_WEIGHT > 1e8


def test_invalid_lts_raises() -> None:
    import pytest
    with pytest.raises(ValueError):
        main_weight_for("kid", 0)
    with pytest.raises(ValueError):
        main_weight_for("kid", 4)


def test_invalid_tier_raises() -> None:
    import pytest
    with pytest.raises(KeyError):
        main_weight_for("medium", 1)
