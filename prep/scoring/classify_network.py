# prep/scoring/classify_network.py
"""Attach a stress tier to each OSM edge from Mellow + CDOT (Phase 4).

Two joins (design §2.1):
  - Mellow → edge is a **way-ID join**: an edge is Mellow-kind X if any of its
    `osm_way_ids` is in the kind-X way set. Best (lowest) tier wins on conflict.
  - CDOT → edge is a **spatial buffer + bearing match**, reusing the
    prep/joins/hin_to_osm.py approach. On-street facilities use the ±30° bearing
    filter; off-street trails are bearing-optional (they may cross streets) and
    map to tier 1 regardless of facility type (review F7).

Then the §1.1 rule combines them (CDOT override + Mellow-path floor) and each
edge becomes a SegmentRecord (lts set, ft_int_str/tf_int_str = None) for the
DbBuilder.
"""

from __future__ import annotations

from collections.abc import Iterable

from shapely.geometry import shape
from shapely.wkt import loads as wkt_loads

# Reuse the tested HIN matcher internals (same package): metric projection,
# bearing math, and buffer/bearing tolerances.
from prep.fetchers.cdot_facilities import CdotFacility
from prep.fetchers.mellow import MellowFeature
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
    MELLOW_KIND_TO_TIER,
    cdot_tier_for_facility,
    combine_final_tier,
)


def _build_way_kind_map(mellow_features: Iterable[MellowFeature]) -> dict[str, str]:
    """Map each OSM way id -> the best (lowest-tier) Mellow kind covering it."""
    way_kind: dict[str, str] = {}
    for feat in mellow_features:
        tier = MELLOW_KIND_TO_TIER.get(feat.kind)
        if tier is None:
            continue
        for way_id in feat.way_ids:
            current = way_kind.get(way_id)
            if current is None or tier < MELLOW_KIND_TO_TIER[current]:
                way_kind[way_id] = feat.kind
    return way_kind


def _mellow_kind_for_edge(edge: OsmEdge, way_kind: dict[str, str]) -> str | None:
    """Best (lowest-tier) Mellow kind across all of an edge's OSM way ids."""
    best_kind: str | None = None
    best_tier = 99
    for way_id in edge.osm_way_ids:
        kind = way_kind.get(way_id)
        if kind is None:
            continue
        tier = MELLOW_KIND_TO_TIER[kind]
        if tier < best_tier:
            best_tier = tier
            best_kind = kind
    return best_kind


def _cdot_tier_for_edges(
    edges: list[OsmEdge],
    facilities: list[CdotFacility],
) -> dict[int, int]:
    """Return road_id -> best (lowest) CDOT tier for edges a facility covers.

    On-street facilities require a ±30° bearing agreement; off-street trails are
    bearing-optional and contribute tier 1.
    """
    from shapely.strtree import STRtree

    if not facilities:
        return {}

    fac_proj = [(f, _project(shape(f.geometry))) for f in facilities]
    fac_geoms = [g for _, g in fac_proj]
    tree = STRtree(fac_geoms)

    result: dict[int, int] = {}
    for edge in edges:
        edge_proj = _project(wkt_loads(edge.geometry_wkt))
        edge_buffered = edge_proj.buffer(SEG_BUFFER_METERS)
        edge_centroid = edge_proj.centroid
        edge_bearing = _bearing(edge_proj, near_point=edge_centroid)

        for idx in tree.query(edge_buffered, predicate="intersects"):
            fac, fac_geom = fac_proj[idx]
            if fac.off_street:
                tier: int | None = 1
            else:
                if _bearing_diff(
                    edge_bearing, _bearing(fac_geom, near_point=edge_centroid)
                ) > SEG_BEARING_TOLERANCE_DEG:
                    continue
                tier = cdot_tier_for_facility(fac.facility_type)
            if tier is None:
                continue
            prev = result.get(edge.road_id)
            if prev is None or tier < prev:
                result[edge.road_id] = tier
    return result


def classify_network(
    edges: list[OsmEdge],
    mellow_features: Iterable[MellowFeature],
    cdot_facilities: list[CdotFacility],
) -> list[SegmentRecord]:
    """Classify every OSM edge into a SegmentRecord with its final stress tier."""
    way_kind = _build_way_kind_map(mellow_features)
    cdot_tiers = _cdot_tier_for_edges(edges, cdot_facilities)

    records: list[SegmentRecord] = []
    for edge in edges:
        mellow_kind = _mellow_kind_for_edge(edge, way_kind)
        cdot_tier = cdot_tiers.get(edge.road_id)
        lts = combine_final_tier(mellow_kind, cdot_tier, edge.highway)
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
    return records
