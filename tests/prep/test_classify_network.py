"""classify_network: OSM edges x Cook County LTS (way-ID join) -> SegmentRecords.

The county layer is OSM-derived, so the join is a plain way_id dict lookup.
Edges whose way ids are absent from the snapshot fall back to the road-class
baseline; classify_network reports the matched/fallback split as ClassifyStats.
"""

from prep.graph.osm_builder import OsmEdge
from prep.scoring.classify_network import ClassifyStats, classify_network


def _edge(
    road_id: int,
    way_ids: tuple[str, ...],
    highway: str,
    *,
    osm_id: int | None = None,
) -> OsmEdge:
    """Build an OsmEdge; `osm_id` defaults to the first way id (pass it explicitly
    when `way_ids` is empty)."""
    return OsmEdge(
        road_id=road_id,
        osm_id=int(way_ids[0]) if osm_id is None else osm_id,
        osm_way_ids=way_ids,
        head_node_id=road_id * 10,
        tail_node_id=road_id * 10 + 1,
        name=f"Street {road_id}",
        highway=highway,
        length_m=100.0,
        geometry_wkt="LINESTRING(-87.7 41.9, -87.69 41.9)",
    )


def test_classify_network_joins_by_way_id_and_tracks_match_rate() -> None:
    edges = [
        _edge(1, ("100",), "residential"),        # matched -> LTS 2
        _edge(2, ("200", "300"), "residential"),  # multi-way, worst -> LTS 4
        _edge(3, ("999",), "secondary"),          # unmatched -> road class 3
    ]
    way_lts = {"100": 2, "200": 1, "300": 4}

    records, stats = classify_network(edges, way_lts)

    assert [r.lts for r in records] == [2, 4, 3]
    assert stats == ClassifyStats(matched=2, fallback=1)


def test_classify_stats_match_rate_percent() -> None:
    assert ClassifyStats(matched=3, fallback=1).match_rate_pct == 75.0
    assert ClassifyStats(matched=0, fallback=0).match_rate_pct == 0.0


def test_classify_network_preserves_edge_fields() -> None:
    edges = [_edge(7, ("100",), "residential")]
    records, _ = classify_network(edges, {"100": 1})
    r = records[0]
    assert r.road_id == 7
    assert r.osm_id == 100
    assert r.head_int_id == 70
    assert r.tail_int_id == 71
    assert r.name == "Street 7"
    assert r.highway == "residential"
    assert r.geometry_wkt == "LINESTRING(-87.7 41.9, -87.69 41.9)"


def test_edge_with_no_way_ids_falls_back_to_road_class() -> None:
    """An edge carrying no OSM way ids can't join at all — road class, not a crash."""
    records, stats = classify_network([_edge(5, (), "residential", osm_id=0)], {"100": 4})
    assert records[0].lts == 1
    assert stats == ClassifyStats(matched=0, fallback=1)
