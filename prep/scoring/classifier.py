"""Pure LTS classifier for the Cook County LTS (2023) scoring model.

LTS scale: 1 = least stress, 4 = most (standard Mineta/UMN 4-level scale).
See design docs/specs/2026-07-29-cook-county-lts4-design.md §3.

Two signals, in order:
  1. Cook County LTS by OSM way-ID join (baseline), falling back to the OSM
     road class for ways absent from the 2023 snapshot.
  2. CDOT bike facilities as an **improve-only** override: a facility can
     lower a street's LTS but never raise it. Rationale — CDOT's Jan-2025
     layer knows about lanes built after the county's 2023 OSM snapshot, which
     is new information; but a facility type says nothing about traffic speed,
     volume, or lane count, which the county's rating already accounts for. So
     a sharrow can't make a hostile arterial look calm, and a signed shared
     lane can't downgrade a quiet residential street.

No geometry / no I/O — pure functions over the way_id->lts map built by
prep.fetchers.cook_lts.parse_cook_lts and the CDOT facility strings, so the
truth table is exhaustively unit-testable. The spatial matching that decides
*which* edges a CDOT facility covers lives in prep.scoring.classify_network.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

logger = logging.getLogger(__name__)

# Road-class fallback for edges whose OSM way ids don't appear in the 2023
# county snapshot (ways created/renumbered since then). Mirrors what the UMN
# methodology would produce from the road class alone: quiet streets stay
# calm rather than defaulting to worst-case.
ROAD_CLASS_TO_LTS: dict[str, int] = {
    # LTS 1: quiet streets and bike-priority / off-street ways.
    "residential": 1,
    "living_street": 1,
    "cycleway": 1,
    "path": 1,
    "footway": 1,
    "pedestrian": 1,
    # LTS 2: minor through-streets.
    "track": 2,
    "unclassified": 2,
    "tertiary": 2,
    "tertiary_link": 2,
    # LTS 3: secondary arterials.
    "secondary": 3,
    "secondary_link": 3,
    # LTS 4: major arterials and limited-access roads.
    "primary": 4,
    "primary_link": 4,
    "trunk": 4,
    "trunk_link": 4,
    "motorway": 4,
    "motorway_link": 4,
    "busway": 4,
}
# Unknown or missing ``highway`` -> conservative worst case.
ROAD_CLASS_BASELINE_DEFAULT = 4

# CDOT facility type -> the LTS it can pull a street *down* to (user decision
# 2026-07-29). Keyed on the live ``BIKE_DSPLY`` vocabulary (abbreviated single
# words) AND the fallback ``DISPLAYROU`` vocabulary (full names, 2023 layer) so
# either CDOT layer resolves. Keys are normalized by ``_normalize``.
#
# SHARED (sharrow) is deliberately ABSENT rather than mapped: it is paint with
# no physical protection, so it earns no upgrade, and under improve-only
# semantics a mapping would be a no-op at best and misleading at worst.
CDOT_FACILITY_TO_LTS: dict[str, int] = {
    # BIKE_DSPLY (Bikeway_Network_2024_Final_Public, Jan 2025)
    "PROTECTED": 1,
    "NEIGHBORHOOD": 1,
    "BUFFERED": 2,
    "BIKE": 2,
    # DISPLAYROU fallback (Chicago_Bike_Facilities_2023)
    "PROTECTED BIKE LANE": 1,
    "NEIGHBORHOOD GREENWAY": 1,
    "BUFFERED BIKE LANE": 2,
    "BIKE LANE": 2,
}
# Off-street trails are LTS 1 regardless of any facility attribute.
CDOT_OFF_STREET_LTS = 1
# Facility values we know about but deliberately do not override on, so an
# unknown-value warning stays meaningful.
CDOT_FACILITY_NO_OVERRIDE = frozenset({"SHARED", "SHARED-LANE", "SHARED LANE"})


def _normalize(facility: str) -> str:
    """Upper-case and collapse internal/surrounding whitespace."""
    return " ".join(facility.upper().split())


def cdot_lts_for_facility(facility: str | None, *, off_street: bool = False) -> int | None:
    """The LTS a CDOT facility can improve a street to, or None for no override.

    Off-street trails are LTS 1 without consulting ``facility``. Sharrows and
    unknown facility strings return None (no override); unknown values are
    logged so a CDOT vocabulary change is visible in the prep run.
    """
    if off_street:
        return CDOT_OFF_STREET_LTS
    if facility is None:
        return None
    key = _normalize(facility)
    lts = CDOT_FACILITY_TO_LTS.get(key)
    if lts is None and key not in CDOT_FACILITY_NO_OVERRIDE:
        logger.warning("Unknown CDOT facility %r; no LTS override applied", facility)
    return lts


def apply_cdot_override(baseline_lts: int, cdot_lts: int | None) -> int:
    """Combine the county baseline with a CDOT facility, improve-only.

    ``min`` is the whole rule: CDOT may lower the LTS (it knows about lanes
    built after the 2023 county snapshot) but never raise it (it knows nothing
    about traffic speed, volume, or lane count, which the county rating does).
    """
    if cdot_lts is None:
        return baseline_lts
    return min(baseline_lts, cdot_lts)


def road_class_baseline_lts(highway: str | None) -> int:
    """Fallback LTS from the OSM ``highway`` class (None/unknown -> 4)."""
    if not highway:
        return ROAD_CLASS_BASELINE_DEFAULT
    return ROAD_CLASS_TO_LTS.get(highway, ROAD_CLASS_BASELINE_DEFAULT)


def lts_for_edge(
    osm_way_ids: Iterable[str],
    way_lts: dict[str, int],
    highway: str | None,
) -> tuple[int, bool]:
    """Return ``(lts, matched)`` for one OSM edge.

    ``osm_way_ids`` are the edge's OSM way ids (simplified osmnx edges carry
    several); ``way_lts`` is the county way_id->lts map. The edge takes the
    **worst (max)** LTS over its matched ways — a segment is as stressful as
    its worst stretch. When no way matches (2023->now way-id drift), fall
    back to the road-class baseline and report ``matched=False`` so the
    caller can track the match rate.
    """
    worst: int | None = None
    for way_id in osm_way_ids:
        lts = way_lts.get(way_id)
        if lts is not None and (worst is None or lts > worst):
            worst = lts
    if worst is not None:
        return worst, True
    return road_class_baseline_lts(highway), False
