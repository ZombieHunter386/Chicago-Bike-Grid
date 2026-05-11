from prep.lts.ingest import SegmentRecord
from prep.lts.synthesize_intersections import synthesize_intersections


def _seg(
    road_id: int,
    coords: list[tuple[float, float]],
    *,
    head_int_id: int,
    tail_int_id: int,
    osm_id: int = 100,
    lts: int = 2,
    ft_int_str: int | None = None,
    tf_int_str: int | None = None,
) -> SegmentRecord:
    """Compact helper for creating SegmentRecord test fixtures."""
    line = "LINESTRING(" + ", ".join(f"{x} {y}" for x, y in coords) + ")"
    return SegmentRecord(
        road_id=road_id,
        osm_id=osm_id,
        head_int_id=head_int_id,
        tail_int_id=tail_int_id,
        name=None,
        lts=lts,
        highway=None,
        speed=None,
        ft_int_str=ft_int_str,
        tf_int_str=tf_int_str,
        geometry_wkt=line,
        raw_properties={},
    )


def test_synthesize_aggregates_per_pfb_intersection_id() -> None:
    """Three segments meeting at PFB intersection 100 should yield one
    IntersectionRecord with osm_id=100 and lts_approach = max of contributions."""
    segs = [
        # Segment 1 ends at int 100 (FT direction): contributes ft_int_str=2 to int 100
        _seg(road_id=1, coords=[(0.0, 0.0), (0.001, 0.0)],
             head_int_id=10, tail_int_id=100, ft_int_str=2),
        # Segment 2 ends at int 100 (FT direction): contributes ft_int_str=4 to int 100
        _seg(road_id=2, coords=[(0.0, 0.001), (0.001, 0.0)],
             head_int_id=11, tail_int_id=100, ft_int_str=4),
        # Segment 3 starts at int 100 (TF direction): contributes tf_int_str=3 to int 100
        _seg(road_id=3, coords=[(0.001, 0.0), (0.002, 0.0)],
             head_int_id=100, tail_int_id=12, tf_int_str=3),
    ]
    nodes = synthesize_intersections(segs)
    by_id = {n.osm_id: n for n in nodes}
    assert 100 in by_id
    # max(2, 4, 3) = 4
    assert by_id[100].lts_approach == 4
    assert by_id[100].raw_properties["contribution_count"] == 3


def test_synthesize_returns_one_record_per_unique_int_id() -> None:
    segs = [
        _seg(road_id=1, coords=[(0.0, 0.0), (1.0, 0.0)],
             head_int_id=10, tail_int_id=20, ft_int_str=3, tf_int_str=2),
    ]
    nodes = synthesize_intersections(segs)
    ids = sorted(n.osm_id for n in nodes)
    assert ids == [10, 20]


def test_synthesize_no_contributions_defaults_to_lts1() -> None:
    """A PFB intersection appears in segments with both ft_int_str and tf_int_str
    NULL — record still emitted (so it can be referenced as a graph node) with
    lts_approach=1 (no-stress default)."""
    segs = [
        _seg(road_id=1, coords=[(0.0, 0.0), (1.0, 0.0)],
             head_int_id=10, tail_int_id=20, ft_int_str=None, tf_int_str=None),
    ]
    nodes = synthesize_intersections(segs)
    assert {n.osm_id for n in nodes} == {10, 20}
    for n in nodes:
        assert n.lts_approach == 1
        assert n.raw_properties["contribution_count"] == 0


def test_synthesize_geometry_taken_from_segment_endpoints() -> None:
    segs = [
        _seg(road_id=1, coords=[(0.0, 0.0), (1.0, 2.0)],
             head_int_id=10, tail_int_id=20, ft_int_str=2, tf_int_str=3),
    ]
    nodes = synthesize_intersections(segs)
    by_id = {n.osm_id: n for n in nodes}
    assert by_id[10].geometry_wkt == "POINT (0 0)"
    assert by_id[20].geometry_wkt == "POINT (1 2)"


def test_synthesize_skips_invalid_geometry() -> None:
    """Segments with malformed WKT should be silently skipped."""
    bad = SegmentRecord(
        road_id=1, osm_id=1, head_int_id=10, tail_int_id=20,
        name=None, lts=2, highway=None, speed=None,
        ft_int_str=3, tf_int_str=2,
        geometry_wkt="NOT_A_VALID_WKT",
        raw_properties={},
    )
    good = _seg(road_id=2, coords=[(0.0, 0.0), (1.0, 0.0)],
                head_int_id=30, tail_int_id=40, ft_int_str=4, tf_int_str=1)
    nodes = synthesize_intersections([bad, good])
    # Only the good segment contributes — 2 nodes (head and tail of the good seg).
    assert {n.osm_id for n in nodes} == {30, 40}


def test_synthesize_deterministic_order() -> None:
    """Output is sorted by osm_id for deterministic INSERT OR REPLACE behavior."""
    segs = [
        _seg(road_id=1, coords=[(0.0, 0.0), (1.0, 0.0)],
             head_int_id=200, tail_int_id=50, ft_int_str=2, tf_int_str=2),
        _seg(road_id=2, coords=[(1.0, 0.0), (2.0, 0.0)],
             head_int_id=50, tail_int_id=10, ft_int_str=2, tf_int_str=2),
    ]
    nodes = synthesize_intersections(segs)
    assert [n.osm_id for n in nodes] == [10, 50, 200]
