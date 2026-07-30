# prep/scoring/classify_network.py
"""Attach a final LTS (1-4) to each OSM edge (design 2026-07-29 §3).

Two joins:
  - **Cook County LTS** (baseline) is a **way-ID join**: the county layer is
    OSM-derived, so ``way_id`` matches our edges' ``osm_way_ids`` exactly — a
    dict lookup, no geometry. Unmatched edges (way-id drift since the 2023
    snapshot) fall back to the road-class baseline.
  - **CDOT facilities** (improve-only override) is a **spatial buffer + bearing
    match**, reusing the tested prep/joins/hin_to_osm.py internals. On-street
    facilities require ±30° bearing agreement with the edge; off-street trails
    are bearing-optional (they cross streets) and contribute LTS 1.

The override can only lower an edge's LTS, never raise it — see
``prep.scoring.classifier.apply_cdot_override`` for the rationale.

ClassifyStats reports both the county match rate and how many edges CDOT
actually improved, so each source's contribution is visible every run.
"""

from __future__ import annotations

from dataclasses import dataclass

from shapely.geometry import shape
from shapely.wkt import loads as wkt_loads

# Reuse the tested HIN matcher internals (same package): metric projection,
# bearing math, and buffer/bearing tolerances.
from prep.fetchers.cdot_facilities import CdotFacility
from prep.graph.osm_builder import OsmEdge
from prep.joins.hin_to_osm import (
    SEG_BEARING_TOLERANCE_DEG,
    SEG_BUFFER_METERS,
    _bearing,
    _bearing_diff,
    _project,
)
from prep.lts.ingest import SegmentRecord
from prep.scoring.classifier import (
    apply_cdot_override,
    cdot_lts_for_facility,
    lts_for_edge,
)


@dataclass(frozen=True)
class ClassifyStats:
    """Per-source contribution to the classified network.

    ``matched``/``fallback``: edges whose LTS baseline came from a Cook County
    way_id vs. from the OSM road-class fallback. ``cdot_improved``: edges whose
    final LTS was lowered by a CDOT facility (a subset of all edges, cutting
    across the matched/fallback split).
    """

    matched: int
    fallback: int
    cdot_improved: int = 0

    @property
    def total(self) -> int:
        return self.matched + self.fallback

    @property
    def match_rate_pct(self) -> float:
        # An empty network reads as 0% (failure), not a vacuous 100%: no edges
        # means the OSM fetch or the county join broke, and prep_report should
        # show that as a bad number. Note HinMatchReport.segment_match_pct takes
        # the opposite convention (empty -> 100.0) because there "nothing to
        # match" is a legitimately complete outcome; the divergence is deliberate.
        return (100.0 * self.matched / self.total) if self.total else 0.0


def cdot_lts_for_edges(
    edges: list[OsmEdge],
    facilities: list[CdotFacility],
) -> dict[int, int]:
    """Return ``road_id -> best (lowest) CDOT override LTS`` for covered edges.

    On-street facilities require a ±30° bearing agreement so a lane on a
    cross-street doesn't bleed onto its neighbours; off-street trails are
    bearing-optional and contribute LTS 1. Edges with no covering facility (or
    only sharrows / unknown vocabulary) are absent from the result.
    """
    from shapely.strtree import STRtree

    if not facilities:
        return {}

    fac_proj = [(f, _project(shape(f.geometry))) for f in facilities]
    tree = STRtree([g for _, g in fac_proj])

    result: dict[int, int] = {}
    for edge in edges:
        edge_proj = _project(wkt_loads(edge.geometry_wkt))
        edge_buffered = edge_proj.buffer(SEG_BUFFER_METERS)
        edge_centroid = edge_proj.centroid
        edge_bearing = _bearing(edge_proj, near_point=edge_centroid)

        for idx in tree.query(edge_buffered, predicate="intersects"):
            fac, fac_geom = fac_proj[idx]
            if not fac.off_street and _bearing_diff(
                edge_bearing, _bearing(fac_geom, near_point=edge_centroid)
            ) > SEG_BEARING_TOLERANCE_DEG:
                continue
            lts = cdot_lts_for_facility(fac.facility_type, off_street=fac.off_street)
            if lts is None:
                continue
            prev = result.get(edge.road_id)
            if prev is None or lts < prev:
                result[edge.road_id] = lts
    return result


def classify_network(
    edges: list[OsmEdge],
    way_lts: dict[str, int],
    cdot_facilities: list[CdotFacility] | None = None,
) -> tuple[list[SegmentRecord], ClassifyStats]:
    """Classify every OSM edge into a SegmentRecord with its final LTS 1-4."""
    cdot_lts = cdot_lts_for_edges(edges, cdot_facilities or [])

    records: list[SegmentRecord] = []
    matched_count = 0
    fallback_count = 0
    cdot_improved_count = 0
    for edge in edges:
        baseline, matched = lts_for_edge(edge.osm_way_ids, way_lts, edge.highway)
        if matched:
            matched_count += 1
        else:
            fallback_count += 1

        lts = apply_cdot_override(baseline, cdot_lts.get(edge.road_id))
        if lts < baseline:
            cdot_improved_count += 1

        records.append(
            SegmentRecord(
                road_id=edge.road_id,
                osm_id=edge.osm_id,
                head_int_id=edge.head_node_id,
                tail_int_id=edge.tail_node_id,
                name=edge.name,
                lts=lts,
                highway=edge.highway,
                speed=None,
                ft_int_str=None,
                tf_int_str=None,
                geometry_wkt=edge.geometry_wkt,
                raw_properties={},
            )
        )
    return records, ClassifyStats(
        matched=matched_count,
        fallback=fallback_count,
        cdot_improved=cdot_improved_count,
    )
