# prep/scoring/intersection_tiers.py
"""Derive intersection approach tiers from the classified OSM edges (Phase 4c).

Replaces prep.lts.synthesize_intersections (which read PFB per-approach LTS). An
intersection node's lts_approach is the max (worst) tier of its incident edges;
a node with no incident edges floors to tier 1 so the NOT NULL schema column is
always satisfied (review F6). Node geometry comes from the osmnx node coords.
"""

from __future__ import annotations

from collections.abc import Iterable

from prep.graph.osm_builder import OsmNode
from prep.lts.ingest import IntersectionRecord, SegmentRecord

LTS_APPROACH_FLOOR = 1


def lts_approach_for_node(incident_tiers: Iterable[int]) -> int:
    """Worst (max) incident edge tier, or the floor when there are none."""
    tiers = list(incident_tiers)
    return max(tiers) if tiers else LTS_APPROACH_FLOOR


def build_intersection_records(
    nodes: Iterable[OsmNode],
    segments: Iterable[SegmentRecord],
) -> list[IntersectionRecord]:
    """One IntersectionRecord per node; lts_approach = worst incident edge tier."""
    incident: dict[int, list[int]] = {}
    for seg in segments:
        incident.setdefault(seg.head_int_id, []).append(seg.lts)
        incident.setdefault(seg.tail_int_id, []).append(seg.lts)

    records: list[IntersectionRecord] = []
    for node in nodes:
        records.append(
            IntersectionRecord(
                osm_id=node.node_id,
                lts_approach=lts_approach_for_node(incident.get(node.node_id, [])),
                signalized=None,
                lanes_crossed=None,
                geometry_wkt=node.geometry_wkt,
                raw_properties={},
            )
        )
    return records
