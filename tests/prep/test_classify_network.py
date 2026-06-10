"""Spatial classify: OSM edges x Mellow (way-id join) x CDOT (spatial) (Phase 4).

classify_network attaches a tier to each OSM edge and returns SegmentRecords:
  - Mellow kind from the way-id join (edge.osm_way_ids ∩ mellow ways), best tier
  - CDOT override from a buffer + bearing spatial match (off-street bearing-optional)
  - the Mellow-path floor (a path stays tier 1)
"""

from shapely.geometry import LineString, MultiLineString, mapping

from prep.fetchers.cdot_facilities import CdotFacility
from prep.fetchers.mellow import MellowFeature
from prep.graph.osm_builder import OsmEdge
from prep.lts.ingest import SegmentRecord
from prep.scoring.classify_network import classify_network


def _edge(
    road_id: int,
    way_ids: tuple[str, ...],
    coords: list[tuple[float, float]],
    highway: str = "residential",
) -> OsmEdge:
    line = LineString(coords)
    return OsmEdge(
        road_id=road_id,
        osm_id=int(way_ids[0]),
        osm_way_ids=way_ids,
        head_node_id=road_id * 10,
        tail_node_id=road_id * 10 + 1,
        name=f"Edge {road_id}",
        highway=highway,
        length_m=line.length,
        geometry_wkt=line.wkt,
    )


def _cdot(facility_type: str | None, coords: list[tuple[float, float]], *, off_street: bool = False) -> CdotFacility:
    return CdotFacility(
        facility_type=facility_type,
        geometry=mapping(LineString(coords)),
        off_street=off_street,
    )


# East-west edges spaced ~111m apart in latitude so buffers never overlap neighbors.
EDGES = [
    _edge(1, ("100",), [(-87.6800, 41.9400), (-87.6750, 41.9400)]),  # mellow street, no CDOT -> 1 (calm green)
    _edge(2, ("999",), [(-87.6800, 41.9460), (-87.6750, 41.9460)], "primary"),  # neither; arterial road class -> 3
    _edge(3, ("888",), [(-87.6800, 41.9410), (-87.6750, 41.9410)]),  # CDOT PROTECTED -> 1
    _edge(4, ("200",), [(-87.6800, 41.9420), (-87.6750, 41.9420)]),  # mellow street + CDOT SHARED -> 3
    _edge(5, ("900",), [(-87.6800, 41.9430), (-87.6750, 41.9430)]),  # mellow PATH + CDOT SHARED -> 1 (floor)
    _edge(6, ("777",), [(-87.6800, 41.9440), (-87.6750, 41.9440)]),  # off-street trail (perp) -> 1
    _edge(7, ("666",), [(-87.6800, 41.9450), (-87.6750, 41.9450)], "secondary"),  # BUFFERED perp -> no match; arterial -> 3
]

MELLOW = [
    MellowFeature(kind="path", way_ids=frozenset({"900"}), slug="p", name="Path"),
    MellowFeature(kind="street", way_ids=frozenset({"100", "200"}), slug="s", name="Street"),
    MellowFeature(kind="route", way_ids=frozenset({"300"}), slug="r", name="Route"),
]

CDOT = [
    # parallel, ~2m north of their target edges (within the 10m buffer, same bearing)
    _cdot("PROTECTED", [(-87.6800, 41.94102), (-87.6750, 41.94102)]),  # over edge 3
    _cdot("SHARED", [(-87.6800, 41.94202), (-87.6750, 41.94202)]),  # over edge 4
    _cdot("SHARED", [(-87.6800, 41.94302), (-87.6750, 41.94302)]),  # over edge 5 (path)
    # off-street trail crossing edge 6 perpendicularly (N-S) — bearing-optional must still match
    _cdot(None, [(-87.6775, 41.9438), (-87.6775, 41.9442)], off_street=True),
    # on-street BUFFERED crossing edge 7 perpendicularly — bearing filter must REJECT it
    _cdot("BUFFERED", [(-87.6770, 41.9448), (-87.6770, 41.9452)]),
]


def _tiers() -> dict[int, int]:
    records = classify_network(EDGES, MELLOW, CDOT)
    assert all(isinstance(r, SegmentRecord) for r in records)
    return {r.road_id: r.lts for r in records}


def test_classify_network_tiers() -> None:
    tiers = _tiers()
    assert tiers == {1: 1, 2: 3, 3: 1, 4: 3, 5: 1, 6: 1, 7: 3}


def test_classify_network_path_floor_end_to_end() -> None:
    # edge 5 is a Mellow path overlapped by a CDOT SHARED (tier-3) facility;
    # the path floor keeps it tier 1.
    assert _tiers()[5] == 1


def test_classify_network_road_class_baseline() -> None:
    """An edge in neither Mellow nor CDOT is classified by its OSM road class:
    a quiet residential street is tier 1 (not the old tier-3 default), while an
    arterial is tier 3."""
    edges = [
        _edge(1, ("1",), [(-87.68, 41.95), (-87.675, 41.95)], "residential"),
        _edge(2, ("2",), [(-87.68, 41.96), (-87.675, 41.96)], "secondary"),
        _edge(3, ("3",), [(-87.68, 41.97), (-87.675, 41.97)], "tertiary"),
    ]
    tiers = {r.road_id: r.lts for r in classify_network(edges, [], [])}
    assert tiers == {1: 1, 2: 3, 3: 2}


def test_classify_network_handles_multilinestring_facility() -> None:
    """CDOT facilities can be MultiLineString; the bearing match must not crash.

    Regression (Phase 6 integration build): `_bearing()` called `list(geom.coords)`,
    which raises "Sub-geometries may have coordinate sequences, but multi-part
    geometries do not" on multi-part geometries. An on-street MultiLineString
    facility laid over an edge (same bearing) must still classify it tier 1.
    """
    edge = _edge(1, ("100",), [(-87.6800, 41.9400), (-87.6750, 41.9400)])
    multi = MultiLineString(
        [
            [(-87.6800, 41.94002), (-87.6775, 41.94002)],
            [(-87.6775, 41.94002), (-87.6750, 41.94002)],
        ]
    )
    fac = CdotFacility(facility_type="PROTECTED", geometry=mapping(multi), off_street=False)
    tiers = {r.road_id: r.lts for r in classify_network([edge], [], [fac])}
    assert tiers[1] == 1


def test_classify_network_emits_segment_records() -> None:
    records = {r.road_id: r for r in classify_network(EDGES, MELLOW, CDOT)}
    r = records[1]
    assert r.osm_id == 100
    assert r.head_int_id == 10 and r.tail_int_id == 11
    assert r.ft_int_str is None and r.tf_int_str is None
    assert r.geometry_wkt.startswith("LINESTRING")
    assert r.lts in (1, 2, 3)
