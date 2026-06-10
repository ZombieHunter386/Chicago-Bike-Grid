"""Truth table for prep.scoring.classifier.classify_tier.

Tier scale: 1 = kid-safe (safest), 2 = parent, 3 = high-stress.

Rule (design §1.1):
  Step 0 Road-class baseline: when a street is in neither Mellow nor CDOT, fall
    back to its OSM `highway` class instead of assuming tier 3. Quiet street
    classes (residential/living_street/cycleway/path) -> 1, minor through-streets
    (tertiary/unclassified/track) -> 2, arterials (secondary/primary/trunk) -> 3.
    Unknown/None highway -> 3. (This replaces the old blanket tier-3 default,
    which painted ~88% of the city red.)
  Step 1 Mellow baseline: path->1, street->1, route->2
    (Mellow's own map labels `street` "Mellow streets (calm)" and renders it
    green — the same green as the recommended low-stress network — so it is a
    kid-safe tier-1, not tier-2. `route` is "Main streets, often with bike
    lanes (less calm)", rendered salmon, so it stays tier-2.)
  Step 2 CDOT override (BIKE_DSPLY): PROTECTED/NEIGHBORHOOD->1, BUFFERED/BIKE->2, SHARED->3
  Step 3 Mellow-path floor: a Mellow path is never downgraded below tier 1.
  Precedence: CDOT (if present) > Mellow (if present) > road-class baseline.
  Unknown CDOT facility falls through to the Mellow/road-class baseline.
"""

import pytest

from prep.scoring.classifier import classify_tier


@pytest.mark.parametrize(
    ("mellow_kind", "cdot_facility", "expected"),
    [
        # --- Mellow baseline alone (no CDOT facility) ---
        ("path", None, 1),
        ("street", None, 1),  # "Mellow streets (calm)" — green, kid-safe
        ("route", None, 2),
        (None, None, 3),  # neither -> tier 3
        # --- CDOT facility alone (not in Mellow) ---
        (None, "PROTECTED", 1),
        (None, "NEIGHBORHOOD", 1),
        (None, "BUFFERED", 2),
        (None, "BIKE", 2),
        (None, "SHARED", 3),
        # --- CDOT present: CDOT wins over the Mellow baseline ---
        ("street", "BUFFERED", 2),  # CDOT downgrades a calm street to tier 2
        ("route", "BIKE", 2),
        ("street", "PROTECTED", 1),  # both tier 1
        ("route", "SHARED", 3),  # CDOT downgrades a mellow route
        ("street", "SHARED", 3),  # CDOT downgrades a calm street
        # --- Mellow-path floor: never downgraded below tier 1 ---
        ("path", "SHARED", 1),
        ("path", "BUFFERED", 1),
        ("path", "BIKE", 1),
        ("path", "PROTECTED", 1),
        # --- unknown CDOT facility falls through to Mellow baseline ---
        ("street", "MYSTERY", 1),
        (None, "MYSTERY", 3),
        ("path", "MYSTERY", 1),
    ],
)
def test_classify_tier(mellow_kind: str | None, cdot_facility: str | None, expected: int) -> None:
    # highway is fixed to a tier-3 road class ("secondary") so these rows isolate
    # the Mellow×CDOT logic: the None/None fallback equals the old tier-3 default.
    # Road-class baseline behaviour is covered by the dedicated tests below.
    assert classify_tier(mellow_kind, cdot_facility, "secondary") == expected


@pytest.mark.parametrize(
    ("highway", "expected"),
    [
        # quiet street classes -> kid-safe tier 1
        ("residential", 1),
        ("living_street", 1),
        ("cycleway", 1),
        ("path", 1),
        # minor through-streets -> tier 2
        ("track", 2),
        ("unclassified", 2),
        ("tertiary", 2),
        ("tertiary_link", 2),
        # arterials / limited-access -> tier 3
        ("secondary", 3),
        ("secondary_link", 3),
        ("primary", 3),
        ("trunk", 3),
        # unknown / missing -> conservative tier 3
        (None, 3),
        ("mystery_class", 3),
    ],
)
def test_road_class_baseline_when_no_mellow_or_cdot(highway: str | None, expected: int) -> None:
    """With neither Mellow nor CDOT, a street is classified by its OSM road class."""
    assert classify_tier(None, None, highway) == expected


def test_mellow_and_cdot_take_precedence_over_road_class() -> None:
    # primary-road baseline is tier 3, but a Mellow street upgrades it to green.
    assert classify_tier("street", None, "primary") == 1
    # ...and a CDOT protected lane likewise upgrades the arterial baseline.
    assert classify_tier(None, "PROTECTED", "primary") == 1
    # a Mellow route on an arterial stays tier 2 (less calm main street).
    assert classify_tier("route", None, "primary") == 2
    # with neither source, the arterial road class governs.
    assert classify_tier(None, None, "primary") == 3


@pytest.mark.parametrize(
    ("cdot_facility", "expected"),
    [
        # case-insensitive
        ("protected", 1),
        ("Protected", 1),
        # surrounding / interior whitespace normalized
        ("  PROTECTED  ", 1),
        ("PROTECTED\t", 1),
        # DISPLAYROU full-name fallback vocab (2023 layer)
        ("PROTECTED BIKE LANE", 1),
        ("NEIGHBORHOOD GREENWAY", 1),
        ("BUFFERED BIKE LANE", 2),
        ("BIKE LANE", 2),
        ("SHARED-LANE", 3),
        ("protected bike lane", 1),  # full-name, case-insensitive
    ],
)
def test_classify_tier_normalizes_cdot_vocab(cdot_facility: str, expected: int) -> None:
    """CDOT facility strings resolve regardless of case/whitespace, and the
    fallback DISPLAYROU full-name vocab maps to the same tiers as BIKE_DSPLY."""
    assert classify_tier(None, cdot_facility, "secondary") == expected


def test_unknown_cdot_facility_logs_and_falls_through(caplog: pytest.LogCaptureFixture) -> None:
    import logging

    with caplog.at_level(logging.WARNING):
        assert classify_tier("street", "MYSTERY", "secondary") == 1
    assert any("MYSTERY" in rec.message for rec in caplog.records)
