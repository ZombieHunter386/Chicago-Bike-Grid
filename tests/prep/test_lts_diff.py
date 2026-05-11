from pathlib import Path

from prep.db.builder import DbBuilder
from prep.lts.ingest import SegmentRecord
from prep.reporting.lts_diff import diff_lts_against_previous


def _build_db_with_segments(db_path: Path, segments: list[tuple[int, int]]) -> None:
    """Build a minimal DB with given (road_id, lts) pairs."""
    builder = DbBuilder(db_path)
    builder.create_schema()
    recs = [
        SegmentRecord(
            road_id=road_id,
            osm_id=road_id * 10,         # arbitrary; non-unique would also be fine
            head_int_id=road_id * 100,   # synthetic but valid (NOT NULL in schema)
            tail_int_id=road_id * 100 + 1,
            name=None,
            lts=lts,
            highway=None,
            speed=None,
            ft_int_str=None,
            tf_int_str=None,
            geometry_wkt="LINESTRING(-87.63 41.88, -87.62 41.88)",
            raw_properties={},
        )
        for road_id, lts in segments
    ]
    builder.insert_streets(recs)
    builder.close()


def test_lts_diff_no_previous_db_returns_empty_diff(tmp_path: Path) -> None:
    current = tmp_path / "current.db"
    _build_db_with_segments(current, [(1, 1), (2, 2), (3, 4)])

    diff = diff_lts_against_previous(current_db=current, previous_db=tmp_path / "missing.db")
    assert diff.total_segments == 3
    assert diff.changed == []
    assert diff.added == [1, 2, 3]
    assert diff.removed == []


def test_lts_diff_detects_lts_changes(tmp_path: Path) -> None:
    previous = tmp_path / "prev.db"
    current = tmp_path / "curr.db"
    _build_db_with_segments(previous, [(1, 2), (2, 3), (3, 4)])
    _build_db_with_segments(current, [(1, 4), (2, 3), (4, 1)])

    diff = diff_lts_against_previous(current_db=current, previous_db=previous)
    assert diff.total_segments == 3
    assert (1, 2, 4) in diff.changed
    assert diff.removed == [3]
    assert diff.added == [4]
    assert all(c[0] != 2 for c in diff.changed)
