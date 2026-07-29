from shapely.geometry import LineString, MultiLineString, Point

from prep.joins.hin_to_osm import (
    HinIntersectionFeature,
    HinSegmentFeature,
    OsmIntersection,
    OsmSegment,
    join_hin_intersections_to_osm,
    join_hin_segments_to_osm,
)


def test_join_segments_matches_overlapping_parallel_lines() -> None:
    # OSM segment running east-west at y=41.975
    osm = [
        OsmSegment(osm_id=1, geometry=LineString([(-87.689, 41.975), (-87.679, 41.975)])),
    ]
    # HIN segment overlapping the same line, slightly offset (5m N)
    hin = [
        HinSegmentFeature(
            feature_id="h1",
            geometry=LineString([(-87.689, 41.97505), (-87.679, 41.97505)]),
            modal_flags={"bike": True, "ped": True},
            severity_rank=4,
        ),
    ]
    matches = list(join_hin_segments_to_osm(hin_segments=hin, osm_segments=osm))
    assert len(matches) == 1
    assert matches[0].osm_id == 1
    assert matches[0].hin_feature_id == "h1"


def test_join_segments_skips_perpendicular_lines() -> None:
    # OSM east-west, HIN north-south through the same point — perpendicular bearing
    osm = [
        OsmSegment(osm_id=1, geometry=LineString([(-87.689, 41.975), (-87.679, 41.975)])),
    ]
    hin = [
        HinSegmentFeature(
            feature_id="h1",
            geometry=LineString([(-87.684, 41.973), (-87.684, 41.977)]),
            modal_flags={"bike": True, "ped": False},
            severity_rank=3,
        ),
    ]
    matches = list(join_hin_segments_to_osm(hin_segments=hin, osm_segments=osm))
    # Bearings differ by 90 degrees — should NOT match
    assert matches == []


def test_join_segments_matches_multilinestring_hin_segment() -> None:
    """A HIN segment digitized in two parts must match without raising.

    `_esri_geom_to_geojson` (prep/fetchers/hin.py) maps an ArcGIS feature with
    multiple `paths` to a MultiLineString, and `LineString.coords` raises
    "Sub-geometries may have coordinate sequences, but multi-part geometries do
    not" on those — `_bearing()` flattens the parts into sub-segments instead.
    """
    # Same east-west OSM segment as the parallel-match case above.
    osm = [
        OsmSegment(osm_id=1, geometry=LineString([(-87.689, 41.975), (-87.679, 41.975)])),
    ]
    # The identical 5m-N offset line, but split into two parts at the midpoint.
    hin = [
        HinSegmentFeature(
            feature_id="h1",
            geometry=MultiLineString(
                [
                    [(-87.689, 41.97505), (-87.684, 41.97505)],
                    [(-87.684, 41.97505), (-87.679, 41.97505)],
                ]
            ),
            modal_flags={"bike": True, "ped": True},
            severity_rank=4,
        ),
    ]
    matches = list(join_hin_segments_to_osm(hin_segments=hin, osm_segments=osm))
    assert len(matches) == 1
    assert matches[0].osm_id == 1
    assert matches[0].hin_feature_id == "h1"


def test_join_intersections_nearest_within_30m() -> None:
    # OSM intersection at exact point
    osm = [OsmIntersection(osm_id=42, geometry=Point(-87.689, 41.975))]
    # HIN intersection ~10m east (0.0001 degrees lng ≈ ~8m at this latitude)
    hin = [
        HinIntersectionFeature(
            feature_id="hi1",
            geometry=Point(-87.6889, 41.975),
            modal_flags={"bike": True, "ped": True},
            severity_rank=5,
        ),
    ]
    matches = list(join_hin_intersections_to_osm(hin_intersections=hin, osm_intersections=osm))
    assert len(matches) == 1
    assert matches[0].osm_id == 42
    assert matches[0].hin_feature_id == "hi1"


def test_join_intersections_skips_far_features() -> None:
    osm = [OsmIntersection(osm_id=42, geometry=Point(-87.689, 41.975))]
    # HIN ~500m away
    hin = [
        HinIntersectionFeature(
            feature_id="hi2",
            geometry=Point(-87.683, 41.975),
            modal_flags={"bike": True, "ped": True},
            severity_rank=3,
        ),
    ]
    matches = list(join_hin_intersections_to_osm(hin_intersections=hin, osm_intersections=osm))
    assert matches == []
