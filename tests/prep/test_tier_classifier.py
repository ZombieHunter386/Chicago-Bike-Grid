"""Truth table for prep.scoring.classifier.classify_tier.

Tier scale: 1 = kid-safe (safest), 2 = parent, 3 = high-stress.

Rule (design §1.1):
  Step 1 Mellow baseline: path->1, street->2, route->2, none->3
  Step 2 CDOT override (BIKE_DSPLY): PROTECTED/NEIGHBORHOOD->1, BUFFERED/BIKE->2, SHARED->3
  Step 3 Mellow-path floor: a Mellow path is never downgraded below tier 1.
  Unknown CDOT facility falls through to the Mellow baseline.
"""

import pytest

from prep.scoring.classifier import classify_tier


@pytest.mark.parametrize(
    ("mellow_kind", "cdot_facility", "expected"),
    [
        # --- Mellow baseline alone (no CDOT facility) ---
        ("path", None, 1),
        ("street", None, 2),
        ("route", None, 2),
        (None, None, 3),  # neither -> tier 3
        # --- CDOT facility alone (not in Mellow) ---
        (None, "PROTECTED", 1),
        (None, "NEIGHBORHOOD", 1),
        (None, "BUFFERED", 2),
        (None, "BIKE", 2),
        (None, "SHARED", 3),
        # --- both agree ---
        ("street", "BUFFERED", 2),
        ("route", "BIKE", 2),
        # --- both disagree: CDOT wins ---
        ("street", "PROTECTED", 1),  # CDOT upgrades
        ("route", "SHARED", 3),  # CDOT downgrades a mellow route
        ("street", "SHARED", 3),
        # --- Mellow-path floor: never downgraded below tier 1 ---
        ("path", "SHARED", 1),
        ("path", "BUFFERED", 1),
        ("path", "BIKE", 1),
        ("path", "PROTECTED", 1),
        # --- unknown CDOT facility falls through to Mellow baseline ---
        ("street", "MYSTERY", 2),
        (None, "MYSTERY", 3),
        ("path", "MYSTERY", 1),
    ],
)
def test_classify_tier(mellow_kind: str | None, cdot_facility: str | None, expected: int) -> None:
    assert classify_tier(mellow_kind, cdot_facility) == expected


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
    assert classify_tier(None, cdot_facility) == expected


def test_unknown_cdot_facility_logs_and_falls_through(caplog: pytest.LogCaptureFixture) -> None:
    import logging

    with caplog.at_level(logging.WARNING):
        assert classify_tier("street", "MYSTERY") == 2
    assert any("MYSTERY" in rec.message for rec in caplog.records)
