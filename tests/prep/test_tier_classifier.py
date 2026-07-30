import pytest

from prep.scoring.classifier import (
    ROAD_CLASS_BASELINE_DEFAULT,
    apply_cdot_override,
    cdot_lts_for_facility,
    lts_for_edge,
    road_class_baseline_lts,
)


@pytest.mark.parametrize(
    ("highway", "expected"),
    [
        ("residential", 1), ("living_street", 1), ("cycleway", 1),
        ("path", 1), ("footway", 1), ("pedestrian", 1),
        ("track", 2), ("unclassified", 2), ("tertiary", 2), ("tertiary_link", 2),
        ("secondary", 3), ("secondary_link", 3),
        ("primary", 4), ("primary_link", 4), ("trunk", 4), ("trunk_link", 4),
        ("motorway", 4), ("motorway_link", 4), ("busway", 4),
    ],
)
def test_road_class_baseline_four_levels(highway: str, expected: int) -> None:
    assert road_class_baseline_lts(highway) == expected


def test_road_class_baseline_unknown_or_missing_is_worst_case() -> None:
    assert ROAD_CLASS_BASELINE_DEFAULT == 4
    assert road_class_baseline_lts(None) == 4
    assert road_class_baseline_lts("weird_new_tag") == 4


@pytest.mark.parametrize("lts", [1, 2, 3, 4])
def test_matched_single_way_uses_county_lts(lts: int) -> None:
    result, matched = lts_for_edge(("100",), {"100": lts}, highway="residential")
    assert result == lts
    assert matched is True


def test_multi_way_edge_takes_worst_lts() -> None:
    # A simplified osmnx edge spanning a calm way and a hostile way is as
    # stressful as its worst stretch.
    result, matched = lts_for_edge(
        ("100", "200", "300"),
        {"100": 1, "300": 4},
        highway="residential",
    )
    assert result == 4
    assert matched is True


def test_unmatched_edge_falls_back_to_road_class() -> None:
    result, matched = lts_for_edge(("999",), {"100": 1}, highway="secondary")
    assert result == 3
    assert matched is False


def test_unmatched_edge_with_unknown_highway_is_lts4() -> None:
    result, matched = lts_for_edge(("999",), {}, highway=None)
    assert result == 4
    assert matched is False


# --- CDOT improve-only override (design §3.3) ---


@pytest.mark.parametrize(
    ("facility", "expected"),
    [
        # Live BIKE_DSPLY vocabulary (Jan-2025 layer).
        ("PROTECTED", 1),
        ("NEIGHBORHOOD", 1),
        ("BUFFERED", 2),
        ("BIKE", 2),
        # Fallback DISPLAYROU vocabulary (2023 layer) must resolve too.
        ("PROTECTED BIKE LANE", 1),
        ("NEIGHBORHOOD GREENWAY", 1),
        ("BUFFERED BIKE LANE", 2),
        ("BIKE LANE", 2),
        # Normalization: case and whitespace runs.
        ("protected", 1),
        ("  buffered  bike   lane ", 2),
    ],
)
def test_cdot_facility_maps_to_override_lts(facility: str, expected: int) -> None:
    assert cdot_lts_for_facility(facility) == expected


@pytest.mark.parametrize("sharrow", ["SHARED", "SHARED-LANE", "shared lane"])
def test_sharrows_never_override(sharrow: str) -> None:
    """Paint with no physical protection earns no upgrade (user, 2026-07-29)."""
    assert cdot_lts_for_facility(sharrow) is None


def test_absent_or_unknown_facility_does_not_override() -> None:
    assert cdot_lts_for_facility(None) is None
    assert cdot_lts_for_facility("SOME NEW CDOT VOCAB") is None


def test_off_street_trail_is_lts1_regardless_of_facility_value() -> None:
    assert cdot_lts_for_facility(None, off_street=True) == 1
    assert cdot_lts_for_facility("SHARED", off_street=True) == 1


@pytest.mark.parametrize("baseline", [1, 2, 3, 4])
def test_override_can_only_improve_never_worsen(baseline: int) -> None:
    """A CDOT facility rated worse than the county baseline must not apply."""
    # A standard bike lane (2) leaves LTS 1 alone but pulls 3 and 4 down to 2.
    assert apply_cdot_override(baseline, 2) == min(baseline, 2)
    # A protected lane always wins down to 1.
    assert apply_cdot_override(baseline, 1) == 1
    # No facility -> baseline untouched.
    assert apply_cdot_override(baseline, None) == baseline


def test_override_worked_example_arterial_with_new_protected_lane() -> None:
    """The case that motivated restoring CDOT: a lane built after the snapshot.

    County rates the arterial LTS 4 from 2023 OSM; CDOT's Jan-2025 layer shows
    a protected lane there now, so the street reads LTS 1.
    """
    baseline, _ = lts_for_edge(("900",), {"900": 4}, highway="primary")
    assert baseline == 4
    assert apply_cdot_override(baseline, cdot_lts_for_facility("PROTECTED")) == 1


def test_override_does_not_downgrade_quiet_street_marked_as_sharrow() -> None:
    """The case improve-only protects: a signed shared lane on a calm street."""
    baseline, _ = lts_for_edge(("901",), {"901": 1}, highway="residential")
    assert apply_cdot_override(baseline, cdot_lts_for_facility("SHARED")) == 1
