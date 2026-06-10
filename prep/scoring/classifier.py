"""Pure tier classifier for the Mellow + CDOT scoring model.

Tier scale: 1 = kid-safe (safest), 2 = parent, 3 = high-stress. See design §1.1.
No geometry / no I/O — a pure function over the source-derived strings so it can
be exhaustively unit-tested.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Step 0 — Road-class baseline. When a street is in neither Mellow nor CDOT,
# fall back to its OSM ``highway`` class instead of assuming the worst. This is
# the classic Level-of-Traffic-Stress idea: a quiet residential street is
# low-stress even without bike infrastructure. Replaces the old blanket tier-3
# default, which marked ~88% of the city (mostly quiet residential streets) red.
ROAD_CLASS_TO_TIER: dict[str, int] = {
    # Kid-safe (1): quiet streets and bike-priority / off-street ways.
    "residential": 1,
    "living_street": 1,
    "cycleway": 1,
    "path": 1,
    "footway": 1,
    "pedestrian": 1,
    # Parent (2): minor through-streets.
    "track": 2,
    "unclassified": 2,
    "tertiary": 2,
    "tertiary_link": 2,
    # High-stress (3): arterials and limited-access roads.
    "secondary": 3,
    "secondary_link": 3,
    "primary": 3,
    "primary_link": 3,
    "trunk": 3,
    "trunk_link": 3,
    "motorway": 3,
    "motorway_link": 3,
    "busway": 3,
}
# Unknown or missing ``highway`` -> conservative tier 3.
ROAD_CLASS_BASELINE_DEFAULT = 3

# Step 1 — Mellow baseline (route kind -> tier).
MELLOW_PATH = "path"
MELLOW_KIND_TO_TIER: dict[str, int] = {
    MELLOW_PATH: 1,  # "Off-street bike paths (very calm)" — Mellow renders pink
    "street": 1,  # "Mellow streets (calm)" — Mellow renders this GREEN, the same
    #               green as its recommended low-stress network, so it is a
    #               kid-safe tier 1 (not tier 2). This is the dominant Mellow
    #               category (~6.8k ways); mapping it to tier 2 made our map far
    #               more yellow/red than the real Mellow Bike Map.
    "route": 2,  # "Main streets, often with bike lanes (less calm)" — salmon
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


def road_class_baseline_tier(highway: str | None) -> int:
    """Step 0 — baseline tier from the OSM ``highway`` class (None/unknown -> 3)."""
    if not highway:
        return ROAD_CLASS_BASELINE_DEFAULT
    return ROAD_CLASS_TO_TIER.get(highway, ROAD_CLASS_BASELINE_DEFAULT)


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


def combine_final_tier(
    mellow_kind: str | None,
    cdot_tier: int | None,
    highway: str | None = None,
) -> int:
    """Combine the three signals into a final tier.

    Precedence: CDOT (if present) > Mellow (if present) > road-class baseline.
    A Mellow ``path`` is never downgraded below tier 1 (the path floor).
    """
    if cdot_tier is not None:
        final = cdot_tier
    elif mellow_kind is not None:
        final = mellow_baseline_tier(mellow_kind)
    else:
        final = road_class_baseline_tier(highway)
    if mellow_kind == MELLOW_PATH:
        final = min(final, 1)
    return final


def classify_tier(
    mellow_kind: str | None,
    cdot_facility: str | None,
    highway: str | None = None,
) -> int:
    """Return the final stress tier (1..3) for a street segment.

    ``mellow_kind`` is the Mellow route kind (``path``/``street``/``route``) or
    ``None`` when the street is not in Mellow. ``cdot_facility`` is the CDOT
    ``BIKE_DSPLY`` value or ``None`` when no CDOT facility covers the street.
    ``highway`` is the OSM road class, used as the baseline when neither Mellow
    nor CDOT covers the street.

    Precedence: CDOT > Mellow > road-class baseline. A Mellow ``path`` is never
    downgraded below tier 1 (the path floor). An unknown CDOT facility string
    falls through to the Mellow/road-class baseline.
    """
    cdot_tier = cdot_tier_for_facility(cdot_facility)
    if cdot_facility is not None and cdot_tier is None:
        logger.warning(
            "Unknown CDOT facility %r; falling through to baseline tier %d",
            cdot_facility,
            combine_final_tier(mellow_kind, None, highway),
        )
    return combine_final_tier(mellow_kind, cdot_tier, highway)
