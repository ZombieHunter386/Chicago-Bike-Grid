import sqlite3
from pathlib import Path

import pytest

from prep.db.builder import SCHEMA_VERSION, DbBuilder
from prep.lts.ingest import IntersectionRecord, PoiRecord, SegmentRecord


def test_builder_creates_schema_and_writes_streets(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    builder = DbBuilder(db_path)
    builder.create_schema()

    seg = SegmentRecord(
        road_id=999,
        osm_id=12345,
        head_int_id=1001,
        tail_int_id=1002,
        name="W Foster Ave",
        lts=4,
        highway="primary",
        speed=30,
        ft_int_str=3,
        tf_int_str=3,
        geometry_wkt="LINESTRING(-87.689 41.975, -87.679 41.975)",
        raw_properties={},
    )
    builder.insert_streets([seg])
    builder.close()

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT road_id, osm_id, name, lts, length_m, head_node_osm_id, tail_node_osm_id "
        "FROM streets"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == 999       # road_id
    assert rows[0][1] == 12345     # osm_id (way ID)
    assert rows[0][2] == "W Foster Ave"
    assert rows[0][3] == 4
    assert rows[0][4] > 0          # length computed
    assert rows[0][5] == 1001      # head_int_id
    assert rows[0][6] == 1002      # tail_int_id


def test_builder_writes_intersections(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    builder = DbBuilder(db_path)
    builder.create_schema()

    inter = IntersectionRecord(
        osm_id=999001,
        lts_approach=4,
        signalized=True,
        lanes_crossed=6,
        geometry_wkt="POINT(-87.689 41.975)",
        raw_properties={},
    )
    builder.insert_intersections([inter])
    builder.close()

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT osm_id, lts_approach, signalized FROM intersections"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0] == (999001, 4, 1)


def test_builder_writes_pois(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    builder = DbBuilder(db_path)
    builder.create_schema()

    poi = PoiRecord(
        name="Audubon Elementary",
        category="school",
        address=None,
        geometry_wkt="POINT(-87.683 41.945)",
        source="brokenspoke",
        raw_properties={},
    )
    builder.insert_pois([poi])
    builder.close()

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT name, category, source FROM pois"
    ).fetchall()
    assert rows == [("Audubon Elementary", "school", "brokenspoke")]


def test_builder_records_meta(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    builder = DbBuilder(db_path)
    builder.create_schema()
    builder.record_meta("hin", record_count=42, status="OK")
    builder.record_schema_meta(code_version="0.1.0")
    builder.close()

    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT source, record_count, status FROM meta").fetchall()
    assert rows == [("hin", 42, "OK")]
    sm = conn.execute("SELECT schema_version FROM schema_meta").fetchall()
    assert sm == [(SCHEMA_VERSION,)]


def test_builder_writes_hin_features_round_trip(tmp_path: Path) -> None:
    """HIN features land in the hin_features table; geometry round-trips
    through WKB."""
    from shapely import wkb
    from shapely.geometry import LineString, Point

    from prep.joins.hin_to_osm import HinIntersectionFeature, HinSegmentFeature

    db_path = tmp_path / "test.db"
    builder = DbBuilder(db_path)
    builder.create_schema()

    seg = HinSegmentFeature(
        feature_id="s1",
        geometry=LineString([(-87.689, 41.975), (-87.679, 41.975)]),
        modal_flags={"bike": True, "ped": False},
        severity_rank=4,
    )
    intx = HinIntersectionFeature(
        feature_id="i1",
        geometry=Point(-87.689, 41.975),
        modal_flags={"bike": False, "ped": True},
        severity_rank=3,
    )
    builder.insert_hin_features([seg], [intx])
    builder.close()

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT feature_id, kind, modal_bike, modal_ped, severity_rank, source_geom "
        "FROM hin_features ORDER BY feature_id"
    ).fetchall()
    assert len(rows) == 2

    # i1 first (alphabetical)
    fid, kind, mb, mp, sev, geom_blob = rows[0]
    assert fid == "i1"
    assert kind == "intersection"
    assert (mb, mp) == (0, 1)
    assert sev == 3
    geom = wkb.loads(geom_blob)
    assert geom.geom_type == "Point"
    assert geom.x == pytest.approx(-87.689)

    fid, kind, mb, mp, sev, _ = rows[1]
    assert fid == "s1"
    assert kind == "segment"
    assert (mb, mp) == (1, 0)
    assert sev == 4


def test_builder_hin_features_segment_and_intersection_with_same_id_coexist(
    tmp_path: Path,
) -> None:
    """CMAP's segment and intersection layers both number OBJECTID from 1 —
    feature_id alone collides between kinds. The composite (feature_id, kind)
    primary key must let both rows coexist."""
    from shapely.geometry import LineString, Point

    from prep.joins.hin_to_osm import HinIntersectionFeature, HinSegmentFeature

    db_path = tmp_path / "test.db"
    builder = DbBuilder(db_path)
    builder.create_schema()

    # Same feature_id "1" for both kinds — pre-fix, intersection would clobber segment.
    seg = HinSegmentFeature(
        feature_id="1",
        geometry=LineString([(-87.689, 41.975), (-87.679, 41.975)]),
        modal_flags={"bike": True, "ped": False},
        severity_rank=4,
    )
    intx = HinIntersectionFeature(
        feature_id="1",
        geometry=Point(-87.689, 41.975),
        modal_flags={"bike": False, "ped": True},
        severity_rank=3,
    )
    builder.insert_hin_features([seg], [intx])
    builder.close()

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT feature_id, kind, modal_bike, modal_ped FROM hin_features "
        "ORDER BY kind"
    ).fetchall()
    # BOTH rows should exist after the fix.
    assert rows == [
        ("1", "intersection", 0, 1),
        ("1", "segment", 1, 0),
    ]
