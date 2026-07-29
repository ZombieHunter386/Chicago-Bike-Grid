"""Pure LTS classifier for the Cook County LTS (2023) scoring model.

LTS scale: 1 = least stress, 4 = most (standard Mineta/UMN 4-level scale).
See design docs/specs/2026-07-29-cook-county-lts4-design.md §3.

No geometry / no I/O — pure functions over the way_id->lts map built by
prep.fetchers.cook_lts.parse_cook_lts, so the truth table is exhaustively
unit-testable.
"""

from __future__ import annotations

from collections.abc import Iterable

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
