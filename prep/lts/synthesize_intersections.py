"""Aggregate IntersectionRecord nodes from PFB segments.

PFB's neighborhood_ways shapefile emits per-row intersection node IDs
(INTERSECTI = from-node, INTERSE_01 = to-node) and per-direction approach
LTS values (TF_INT_STR = approach LTS at from-node, FT_INT_STR = approach
LTS at to-node). This module walks all segments and produces one
IntersectionRecord per distinct PFB intersection ID, with:

  - geometry: the segment endpoint at that intersection (segments at the
    same PFB intersection ID have identical endpoint coordinates)
  - lts_approach: max across incident-segment approach LTS contributions

Aggregation rule (matches spec §4.1): the worst stress at any approach
drives routing — so intersection LTS is the max of all contributing
approach values.
"""
from __future__ import annotations

from collections.abc import Iterable

from shapely import wkt
from shapely.geometry import LineString, Point

from prep.lts.ingest import IntersectionRecord, SegmentRecord


def synthesize_intersections(
    segments: Iterable[SegmentRecord],
) -> list[IntersectionRecord]:
    """Build IntersectionRecord list from segment-embedded PFB intersection IDs.

    For each unique PFB intersection ID across all segments, produce one
    IntersectionRecord. lts_approach is the max of all incident-segment
    approach contributions (TF_INT_STR for from-node side, FT_INT_STR for
    to-node side; per-direction NULLs are ignored).

    Returns IntersectionRecords sorted by osm_id for deterministic order.
    """
    # int_id -> (Point geometry, list of approach-LTS contributions)
    geoms: dict[int, Point] = {}
    contributions: dict[int, list[int]] = {}

    for seg in segments:
        try:
            geom = wkt.loads(seg.geometry_wkt)
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(geom, LineString) or len(geom.coords) < 2:
            continue

        head_pt = Point(geom.coords[0][0], geom.coords[0][1])
        tail_pt = Point(geom.coords[-1][0], geom.coords[-1][1])

        # First-write-wins for geometry (PFB segments at the same intersection
        # share endpoint coordinates within float-rep noise; first observed
        # is fine).
        geoms.setdefault(seg.head_int_id, head_pt)
        geoms.setdefault(seg.tail_int_id, tail_pt)

        # tf_int_str = approach LTS at the FROM end (head_int_id);
        # ft_int_str = approach LTS at the TO end (tail_int_id).
        if seg.tf_int_str is not None:
            contributions.setdefault(seg.head_int_id, []).append(seg.tf_int_str)
        if seg.ft_int_str is not None:
            contributions.setdefault(seg.tail_int_id, []).append(seg.ft_int_str)

    out: list[IntersectionRecord] = []
    for int_id, pt in geoms.items():
        approach_vals = contributions.get(int_id, [])
        # Default to LTS 1 when no approach-LTS contributions exist (the
        # intersection still needs a record so routing can address it; LTS 1
        # is the no-stress floor).
        lts_approach = max(approach_vals) if approach_vals else 1
        out.append(IntersectionRecord(
            osm_id=int_id,
            lts_approach=lts_approach,
            signalized=None,
            lanes_crossed=None,
            geometry_wkt=pt.wkt,
            raw_properties={"contribution_count": len(approach_vals)},
        ))
    out.sort(key=lambda r: r.osm_id)
    return out
