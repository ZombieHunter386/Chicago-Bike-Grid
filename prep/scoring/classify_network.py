# prep/scoring/classify_network.py
"""Attach a Cook County LTS (1-4) to each OSM edge (design 2026-07-29 §3).

One join: the county layer is OSM-derived, so ``way_id`` matches our edges'
``osm_way_ids`` exactly — a dict lookup, no spatial matching. Unmatched edges
(way-id drift since the 2023 snapshot) fall back to the road-class baseline;
the matched/fallback split is returned so prep_report can publish the match
rate every run.
"""

from __future__ import annotations

from dataclasses import dataclass

from prep.graph.osm_builder import OsmEdge
from prep.lts.ingest import SegmentRecord
from prep.scoring.classifier import lts_for_edge


@dataclass(frozen=True)
class ClassifyStats:
    """How many edges matched a county way_id vs. fell back to road class."""

    matched: int
    fallback: int

    @property
    def match_rate_pct(self) -> float:
        total = self.matched + self.fallback
        return (100.0 * self.matched / total) if total else 0.0


def classify_network(
    edges: list[OsmEdge],
    way_lts: dict[str, int],
) -> tuple[list[SegmentRecord], ClassifyStats]:
    """Classify every OSM edge into a SegmentRecord with its final LTS 1-4."""
    records: list[SegmentRecord] = []
    matched_count = 0
    fallback_count = 0
    for edge in edges:
        lts, matched = lts_for_edge(edge.osm_way_ids, way_lts, edge.highway)
        if matched:
            matched_count += 1
        else:
            fallback_count += 1
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
    return records, ClassifyStats(matched=matched_count, fallback=fallback_count)
