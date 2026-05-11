import pytest
from shapely.geometry import LineString, Point

from prep.joins.hin_to_osm import (
    HinIntersectionFeature,
    HinIntersectionMatch,
    HinSegmentFeature,
    HinSegmentMatch,
)
from prep.reporting.hin_match_report import build_hin_match_report


def test_match_report_summarizes_coverage() -> None:
    hin_segs = [
        HinSegmentFeature(feature_id="s1", geometry=LineString([(0,0),(1,0)]), modal_flags={"bike": True, "ped": False}, severity_rank=4),
        HinSegmentFeature(feature_id="s2", geometry=LineString([(0,1),(1,1)]), modal_flags={"bike": False, "ped": True}, severity_rank=3),
        HinSegmentFeature(feature_id="s3", geometry=LineString([(0,2),(1,2)]), modal_flags={"bike": True, "ped": True}, severity_rank=5),
    ]
    hin_ints = [
        HinIntersectionFeature(feature_id="i1", geometry=Point(0,0), modal_flags={"bike": True, "ped": True}, severity_rank=5),
        HinIntersectionFeature(feature_id="i2", geometry=Point(0,5), modal_flags={"bike": True, "ped": True}, severity_rank=4),
    ]
    seg_matches = [
        HinSegmentMatch(osm_id=11, hin_feature_id="s1", modal_flags={"bike": True, "ped": False}, severity_rank=4),
        HinSegmentMatch(osm_id=12, hin_feature_id="s2", modal_flags={"bike": False, "ped": True}, severity_rank=3),
    ]
    int_matches = [
        HinIntersectionMatch(osm_id=21, hin_feature_id="i1", modal_flags={"bike": True, "ped": True}, severity_rank=5),
    ]

    report = build_hin_match_report(
        hin_segments=hin_segs,
        hin_intersections=hin_ints,
        segment_matches=seg_matches,
        intersection_matches=int_matches,
    )

    assert report.segment_match_pct == pytest.approx(2/3 * 100)
    assert report.intersection_match_pct == pytest.approx(50.0)
    assert report.unmatched_segment_ids == ["s3"]
    assert report.unmatched_intersection_ids == ["i2"]

    md = report.to_markdown()
    assert "60" in md or "0.60" in md or "5" in md
    assert "s3" in md
    assert "i2" in md
