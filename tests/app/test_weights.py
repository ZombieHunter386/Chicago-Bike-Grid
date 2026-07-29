import pytest

from app.core.weights import INF_WEIGHT, TIERS, fallback_weight_for, main_weight_for


def test_four_tiers_defined() -> None:
    assert set(TIERS.keys()) == {"kid", "inexperienced", "experienced", "death_wish"}


def test_kid_tier_allows_only_lts1() -> None:
    assert main_weight_for("kid", 1) == 1.0
    for lts in (2, 3, 4):
        assert main_weight_for("kid", lts) == INF_WEIGHT


def test_inexperienced_tier_allows_lts2_blocks_3_and_4() -> None:
    assert main_weight_for("inexperienced", 1) == 1.0
    assert main_weight_for("inexperienced", 2) == 1.2
    assert main_weight_for("inexperienced", 3) == INF_WEIGHT
    assert main_weight_for("inexperienced", 4) == INF_WEIGHT


def test_experienced_tier_allows_lts3_blocks_4() -> None:
    assert main_weight_for("experienced", 1) == 1.0
    assert main_weight_for("experienced", 2) == 1.2
    assert main_weight_for("experienced", 3) == 1.5
    assert main_weight_for("experienced", 4) == INF_WEIGHT


def test_death_wish_tier_allows_all_with_graduated_penalty() -> None:
    assert main_weight_for("death_wish", 1) == 1.0
    assert main_weight_for("death_wish", 2) == 1.2
    assert main_weight_for("death_wish", 3) == 1.5
    assert main_weight_for("death_wish", 4) == 2.0


def test_fallback_weights_penalize_out_of_tier_lts() -> None:
    assert fallback_weight_for("kid", 2) == 5.0
    assert fallback_weight_for("kid", 3) == 20.0
    assert fallback_weight_for("kid", 4) == 40.0
    assert fallback_weight_for("inexperienced", 3) == 10.0
    assert fallback_weight_for("inexperienced", 4) == 20.0
    assert fallback_weight_for("experienced", 4) == 10.0
    # death_wish fallback == main (nothing is out of tier)
    assert fallback_weight_for("death_wish", 4) == 2.0


def test_inf_weight_dominates_any_realistic_path_cost() -> None:
    """Routing detects 'no in-tier path' by checking whether any edge in the
    result carries weight >= INF_WEIGHT. INF_WEIGHT must therefore dwarf
    any plausible weighted cost from a finite-weight path (worst case: the
    largest fallback multiplier over Chicago's diameter)."""
    chicago_diameter_m = 50_000
    worst_case_cost = chicago_diameter_m * 40.0
    assert INF_WEIGHT > worst_case_cost * 100


def test_invalid_lts_raises() -> None:
    with pytest.raises(ValueError):
        main_weight_for("kid", 0)
    with pytest.raises(ValueError):
        main_weight_for("kid", 5)


def test_invalid_tier_raises() -> None:
    with pytest.raises(KeyError):
        main_weight_for("parent", 1)
    with pytest.raises(KeyError):
        main_weight_for("any", 1)
