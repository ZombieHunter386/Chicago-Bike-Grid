"""Pure tier classifier for the Mellow + CDOT scoring model.

Tier scale: 1 = kid-safe (safest), 2 = parent, 3 = high-stress. See design §1.1.
No geometry / no I/O — a pure function over the source-derived strings so it can
be exhaustively unit-tested.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Step 1 — Mellow baseline (route kind -> tier). A street absent from Mellow
# (``None``) is the worst baseline, tier 3.
MELLOW_PATH = "path"
MELLOW_KIND_TO_TIER: dict[str, int] = {
    MELLOW_PATH: 1,  # protected off-street
    "street": 2,  # mellow calm street
    "route": 2,  # official on-street route
}
MELLOW_BASELINE_NONE = 3

# Step 2 — CDOT override. Keyed on the live ``BIKE_DSPLY`` vocabulary (abbreviated
# single words) AND the fallback ``DISPLAYROU`` vocabulary (full names, 2023
# layer) so either CDOT layer resolves. Keys are normalized (see ``_normalize``).
CDOT_FACILITY_TO_TIER: dict[str, int] = {
    # BIKE_DSPLY (Bikeway_Network_2024_Final_Public, Jan 2025)
    "PROTECTED": 1,
    "NEIGHBORHOOD": 1,
    "BUFFERED": 2,
    "BIKE": 2,
    "SHARED": 3,
    # DISPLAYROU fallback (Chicago_Bike_Facilities_2023)
    "PROTECTED BIKE LANE": 1,
    "NEIGHBORHOOD GREENWAY": 1,
    "BUFFERED BIKE LANE": 2,
    "BIKE LANE": 2,
    "SHARED-LANE": 3,
}


def _normalize(facility: str) -> str:
    """Upper-case and collapse internal/surrounding whitespace."""
    return " ".join(facility.upper().split())


def mellow_baseline_tier(mellow_kind: str | None) -> int:
    """Step 1 — the Mellow baseline tier for a route kind (None -> tier 3)."""
    if not mellow_kind:
        return MELLOW_BASELINE_NONE
    return MELLOW_KIND_TO_TIER.get(mellow_kind, MELLOW_BASELINE_NONE)


def cdot_tier_for_facility(cdot_facility: str | None) -> int | None:
    """Step 2 — the CDOT tier for a facility string, or None if absent/unknown."""
    if cdot_facility is None:
        return None
    return CDOT_FACILITY_TO_TIER.get(_normalize(cdot_facility))


def combine_final_tier(mellow_kind: str | None, cdot_tier: int | None) -> int:
    """Combine a Mellow baseline with a resolved CDOT tier (None = no CDOT).

    CDOT wins where present; a Mellow path is never downgraded below tier 1.
    """
    final = cdot_tier if cdot_tier is not None else mellow_baseline_tier(mellow_kind)
    if mellow_kind == MELLOW_PATH:
        final = min(final, 1)
    return final


def classify_tier(mellow_kind: str | None, cdot_facility: str | None) -> int:
    """Return the final stress tier (1..3) for a street segment.

    ``mellow_kind`` is the Mellow route kind (``path``/``street``/``route``) or
    ``None`` when the street is not in Mellow. ``cdot_facility`` is the CDOT
    ``BIKE_DSPLY`` value or ``None`` when no CDOT facility covers the street.

    CDOT wins where it covers the street, except a Mellow ``path`` is never
    downgraded below tier 1 (the path floor). An unknown CDOT facility string
    falls through to the Mellow baseline.
    """
    cdot_tier = cdot_tier_for_facility(cdot_facility)
    if cdot_facility is not None and cdot_tier is None:
        logger.warning(
            "Unknown CDOT facility %r; falling through to Mellow baseline tier %d",
            cdot_facility,
            mellow_baseline_tier(mellow_kind),
        )
    return combine_final_tier(mellow_kind, cdot_tier)
