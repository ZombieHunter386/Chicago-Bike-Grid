import pytest

from prep.scoring.classifier import (
    ROAD_CLASS_BASELINE_DEFAULT,
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
