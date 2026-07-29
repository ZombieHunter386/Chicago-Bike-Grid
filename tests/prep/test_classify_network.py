"""classify_network: OSM edges x Cook County LTS (way-ID join) -> SegmentRecords.

The county layer is OSM-derived, so the join is a plain way_id dict lookup.
Edges whose way ids are absent from the snapshot fall back to the road-class
baseline; classify_network reports the matched/fallback split as ClassifyStats.
"""

from shapely.geometry import LineString, mapping

from prep.fetchers.cdot_facilities import CdotFacility
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
    # Empty network reads as 0%, not a vacuous 100% — see the property's comment.
    assert ClassifyStats(matched=0, fallback=0).match_rate_pct == 0.0


def test_classify_stats_total() -> None:
    assert ClassifyStats(matched=3, fallback=1).total == 4
    assert ClassifyStats(matched=0, fallback=0).total == 0


def test_classify_network_on_empty_network() -> None:
    assert classify_network([], {}) == ([], ClassifyStats(matched=0, fallback=0))


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
    # These stay null here by contract — later passes fill them in
    # (intersection_tiers sets ft_int_str/tf_int_str; speed is unused).
    assert r.speed is None
    assert r.ft_int_str is None
    assert r.tf_int_str is None


def test_edge_with_no_way_ids_falls_back_to_road_class() -> None:
    """An edge carrying no OSM way ids can't join at all — road class, not a crash."""
    records, stats = classify_network([_edge(5, (), "residential", osm_id=0)], {"100": 4})
    assert records[0].lts == 1
    assert stats == ClassifyStats(matched=0, fallback=1)


# --- CDOT improve-only override, spatially matched (design §3.3) ---
#
# Geometry conventions mirror tests/prep/test_hin_to_osm.py: east-west edges
# spaced ~111m apart in latitude so their match buffers never overlap a
# neighbour, and facilities drawn parallel (matching bearing) or perpendicular
# (bearing rejected) as each case requires.


def _geo_edge(
    road_id: int,
    way_ids: tuple[str, ...],
    coords: list[tuple[float, float]],
    highway: str = "residential",
) -> OsmEdge:
    line = LineString(coords)
    return OsmEdge(
        road_id=road_id,
        osm_id=int(way_ids[0]) if way_ids else 0,
        osm_way_ids=way_ids,
        head_node_id=road_id * 10,
        tail_node_id=road_id * 10 + 1,
        name=f"Edge {road_id}",
        highway=highway,
        length_m=line.length,
        geometry_wkt=line.wkt,
    )


def _cdot(
    facility_type: str | None,
    coords: list[tuple[float, float]],
    *,
    off_street: bool = False,
) -> CdotFacility:
    return CdotFacility(
        facility_type=facility_type,
        geometry=mapping(LineString(coords)),
        off_street=off_street,
    )


def test_cdot_override_lowers_lts_but_never_raises_it() -> None:
    edges = [
        # LTS 4 arterial that CDOT now shows as protected -> improved to 1.
        _geo_edge(1, ("100",), [(-87.680, 41.9400), (-87.675, 41.9400)], "primary"),
        # LTS 1 quiet street CDOT marks as a sharrow -> untouched at 1.
        _geo_edge(2, ("200",), [(-87.680, 41.9410), (-87.675, 41.9410)]),
        # LTS 4 arterial with a painted lane -> improved only to 2.
        _geo_edge(3, ("300",), [(-87.680, 41.9420), (-87.675, 41.9420)], "primary"),
        # LTS 1 street with a painted lane -> stays 1 (override would worsen).
        _geo_edge(4, ("400",), [(-87.680, 41.9430), (-87.675, 41.9430)]),
    ]
    way_lts = {"100": 4, "200": 1, "300": 4, "400": 1}
    facilities = [
        _cdot("PROTECTED", [(-87.680, 41.9400), (-87.675, 41.9400)]),
        _cdot("SHARED", [(-87.680, 41.9410), (-87.675, 41.9410)]),
        _cdot("BIKE", [(-87.680, 41.9420), (-87.675, 41.9420)]),
        _cdot("BUFFERED", [(-87.680, 41.9430), (-87.675, 41.9430)]),
    ]

    records, stats = classify_network(edges, way_lts, facilities)

    assert [r.lts for r in records] == [1, 1, 2, 1]
    # Only edges 1 and 3 were actually improved.
    assert stats.cdot_improved == 2
    assert stats.matched == 4


def test_off_street_trail_improves_to_lts1_without_bearing_agreement() -> None:
    """Trails cross streets, so they match regardless of bearing."""
    edge = _geo_edge(1, ("100",), [(-87.680, 41.940), (-87.675, 41.940)], "primary")
    perpendicular_trail = _cdot(
        None, [(-87.6775, 41.9385), (-87.6775, 41.9415)], off_street=True
    )
    records, stats = classify_network([edge], {"100": 4}, [perpendicular_trail])
    assert records[0].lts == 1
    assert stats.cdot_improved == 1


def test_on_street_facility_on_a_cross_street_does_not_bleed_over() -> None:
    """A perpendicular on-street lane fails the ±30° bearing check."""
    edge = _geo_edge(1, ("100",), [(-87.680, 41.940), (-87.675, 41.940)], "primary")
    perpendicular_lane = _cdot("PROTECTED", [(-87.6775, 41.9385), (-87.6775, 41.9415)])
    records, stats = classify_network([edge], {"100": 4}, [perpendicular_lane])
    assert records[0].lts == 4
    assert stats.cdot_improved == 0


def test_best_facility_wins_when_several_cover_one_edge() -> None:
    coords = [(-87.680, 41.940), (-87.675, 41.940)]
    edge = _geo_edge(1, ("100",), coords, "primary")
    facilities = [_cdot("BIKE", coords), _cdot("PROTECTED", coords)]
    records, _ = classify_network([edge], {"100": 4}, facilities)
    assert records[0].lts == 1


def test_cdot_override_applies_to_road_class_fallback_edges_too() -> None:
    """A way absent from the 2023 county snapshot can still be improved."""
    coords = [(-87.680, 41.940), (-87.675, 41.940)]
    edge = _geo_edge(1, ("999",), coords, "primary")  # unmatched -> road class 4
    records, stats = classify_network([edge], {}, [_cdot("PROTECTED", coords)])
    assert records[0].lts == 1
    assert stats == ClassifyStats(matched=0, fallback=1, cdot_improved=1)


def test_no_facilities_argument_is_equivalent_to_empty_list() -> None:
    edges = [_geo_edge(1, ("100",), [(-87.680, 41.940), (-87.675, 41.940)])]
    assert classify_network(edges, {"100": 2}) == classify_network(edges, {"100": 2}, [])
