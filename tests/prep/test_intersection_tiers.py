"""Intersection tiers (Phase 4c).

An intersection node's lts_approach is the max (worst) tier of its incident
edges; a node with no incident edges floors to tier 1 (schema is NOT NULL).
Replaces prep.lts.synthesize_intersections for the OSM graph.
"""

from shapely.wkt import loads as wkt_loads

from prep.graph.osm_builder import OsmNode
from prep.lts.ingest import IntersectionRecord, SegmentRecord
from prep.scoring.intersection_tiers import (
    build_intersection_records,
    lts_approach_for_node,
)


def _seg(road_id: int, head: int, tail: int, lts: int) -> SegmentRecord:
    return SegmentRecord(
        road_id=road_id,
        osm_id=road_id,
        head_int_id=head,
        tail_int_id=tail,
        name=None,
        lts=lts,
        highway=None,
        speed=None,
        ft_int_str=None,
        tf_int_str=None,
        geometry_wkt="LINESTRING(-87.68 41.94, -87.67 41.94)",
        raw_properties={},
    )


def test_lts_approach_is_max_incident() -> None:
    assert lts_approach_for_node([2, 3, 1]) == 3
    assert lts_approach_for_node([1, 1]) == 1


def test_lts_approach_floor_when_no_incident_edges() -> None:
    assert lts_approach_for_node([]) == 1


def test_build_intersection_records() -> None:
    nodes = [
        OsmNode(node_id=10, geometry_wkt="POINT (-87.680 41.940)"),
        OsmNode(node_id=11, geometry_wkt="POINT (-87.670 41.940)"),
        OsmNode(node_id=13, geometry_wkt="POINT (-87.670 41.950)"),
        OsmNode(node_id=99, geometry_wkt="POINT (-87.660 41.960)"),  # isolated
    ]
    segments = [
        _seg(1, head=10, tail=11, lts=2),
        _seg(2, head=11, tail=13, lts=3),
    ]

    records = {r.osm_id: r for r in build_intersection_records(nodes, segments)}
    assert all(isinstance(r, IntersectionRecord) for r in records.values())

    assert records[10].lts_approach == 2  # only incident edge is lts 2
    assert records[11].lts_approach == 3  # incident edges lts 2 and 3 -> worst 3
    assert records[13].lts_approach == 3
    assert records[99].lts_approach == 1  # isolated node floors to 1

    pt = wkt_loads(records[10].geometry_wkt)
    assert (pt.x, pt.y) == (-87.680, 41.940)
    assert records[10].signalized is None
    assert records[10].lanes_crossed is None
