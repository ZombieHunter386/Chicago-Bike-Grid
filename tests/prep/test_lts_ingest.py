# tests/prep/test_lts_ingest.py
import json
from pathlib import Path

from prep.lts.ingest import (
    IntersectionRecord,
    SegmentRecord,
    ingest_cdp_pois,
    ingest_osm_pois,
)

# ---------------------------------------------------------------------------
# Record dataclasses (produced by the scoring layer, consumed by DbBuilder)
# ---------------------------------------------------------------------------

def test_segment_record_dataclass_exists() -> None:
    seg = SegmentRecord(
        road_id=1,
        osm_id=12345,
        head_int_id=10,
        tail_int_id=11,
        name="W Foster Ave",
        lts=2,
        highway="residential",
        speed=None,
        ft_int_str=None,
        tf_int_str=None,
        geometry_wkt="LINESTRING(-87.68 41.94, -87.67 41.94)",
        raw_properties={},
    )
    assert seg.road_id == 1
    assert seg.lts == 2


def test_intersection_record_dataclass_exists() -> None:
    rec = IntersectionRecord(
        osm_id=1,
        lts_approach=3,
        signalized=True,
        lanes_crossed=4,
        geometry_wkt="POINT(-87.689 41.975)",
        raw_properties={},
    )
    assert rec.osm_id == 1
    assert rec.lts_approach == 3


# ---------------------------------------------------------------------------
# OSM POI ingest tests
# ---------------------------------------------------------------------------

def test_ingest_osm_pois_categorizes_by_filename(tmp_path: Path) -> None:
    (tmp_path / "osm_pois_school.geojson").write_text(json.dumps({
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature",
             "properties": {"name": "Audubon Elementary School"},
             "geometry": {"type": "Point", "coordinates": [-87.683, 41.945]}},
        ],
    }))
    (tmp_path / "osm_pois_hospital.geojson").write_text(json.dumps({
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature",
             "properties": {"name": "Swedish Hospital"},
             "geometry": {"type": "Point", "coordinates": [-87.700, 41.970]}},
        ],
    }))

    records = list(ingest_osm_pois(tmp_path))

    assert len(records) == 2
    assert {r.category for r in records} == {"school", "hospital"}
    school_rec = next(r for r in records if r.category == "school")
    assert school_rec.name == "Audubon Elementary School"
    assert school_rec.geometry_wkt.startswith("POINT")
    assert school_rec.source == "osm"


def test_ingest_osm_pois_skips_missing_files(tmp_path: Path) -> None:
    """Empty snapshot dir → empty iterator."""
    assert list(ingest_osm_pois(tmp_path)) == []


def test_ingest_osm_pois_composes_address_from_components(tmp_path: Path) -> None:
    """OSM POIs typically have addr:housenumber + addr:street + addr:city
    instead of a single addr:full field. Address must be composed from these.
    """
    (tmp_path / "osm_pois_school.geojson").write_text(
        '''{"type":"FeatureCollection","features":[
          {"type":"Feature",
           "properties":{
             "osm_id":1,
             "name":"Audubon Elementary",
             "addr:housenumber":"3500",
             "addr:street":"N Hoyne Ave",
             "addr:city":"Chicago"
           },
           "geometry":{"type":"Point","coordinates":[-87.683, 41.945]}
          },
          {"type":"Feature",
           "properties":{
             "osm_id":2,
             "name":"Solo Street School",
             "addr:street":"N Lincoln Ave"
           },
           "geometry":{"type":"Point","coordinates":[-87.680, 41.940]}
          },
          {"type":"Feature",
           "properties":{
             "osm_id":3,
             "name":"Unknown Address School"
           },
           "geometry":{"type":"Point","coordinates":[-87.679, 41.939]}
          }
        ]}'''
    )
    records = list(ingest_osm_pois(tmp_path))
    addrs = {r.name: r.address for r in records}
    assert addrs["Audubon Elementary"] == "3500 N Hoyne Ave, Chicago"
    assert addrs["Solo Street School"] == "N Lincoln Ave"
    assert addrs["Unknown Address School"] is None


# ---------------------------------------------------------------------------
# CDP POI ingest tests
# ---------------------------------------------------------------------------

def test_ingest_cdp_pois_alderman_with_ordinal_name(tmp_path: Path) -> None:
    """Alderman POIs get ordinal-formatted names like '34th Ward Alderman Office'."""
    (tmp_path / "cdp_alderman_offices.geojson").write_text(json.dumps({
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature",
             "properties": {"ward": "34", "alderman": "Conway, William",
                            "address": "121 North LaSalle Street", "city": "Chicago",
                            "state": "IL", "zipcode": "60602"},
             "geometry": {"type": "Point", "coordinates": [-87.6322, 41.8837]}},
            {"type": "Feature",
             "properties": {"ward": "1", "alderman": "Test, A",
                            "address": "1 Foo St", "city": "Chicago"},
             "geometry": {"type": "Point", "coordinates": [-87.6, 41.9]}},
            {"type": "Feature",
             "properties": {"ward": "22", "alderman": "Test, B",
                            "address": "22 Bar St", "city": "Chicago"},
             "geometry": {"type": "Point", "coordinates": [-87.7, 41.95]}},
            {"type": "Feature",
             "properties": {"ward": "11", "alderman": "Test, C",
                            "address": "11 Baz St", "city": "Chicago"},
             "geometry": {"type": "Point", "coordinates": [-87.65, 41.92]}},
        ],
    }))
    records = list(ingest_cdp_pois(tmp_path))
    assert len(records) == 4
    by_ward = {r.raw_properties["ward"]: r for r in records}
    assert by_ward["34"].name == "34th Ward Alderman Office"
    assert by_ward["1"].name == "1st Ward Alderman Office"
    assert by_ward["22"].name == "22nd Ward Alderman Office"
    assert by_ward["11"].name == "11th Ward Alderman Office"  # 11/12/13 → 11th/12th/13th
    assert all(r.category == "alderman" for r in records)
    assert all(r.source == "cdp" for r in records)


def test_ingest_cdp_pois_library_branch_name(tmp_path: Path) -> None:
    """Library POIs use the branch_ field for the name."""
    (tmp_path / "cdp_libraries.geojson").write_text(json.dumps({
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature",
             "properties": {"branch_": "Albany Park", "address": "3401 W. Foster Ave.",
                            "city": "Chicago", "state": "IL", "zip": "60625"},
             "geometry": {"type": "Point", "coordinates": [-87.71, 41.97]}},
        ],
    }))
    records = list(ingest_cdp_pois(tmp_path))
    assert len(records) == 1
    assert records[0].name == "Albany Park"
    assert records[0].category == "library"
    assert records[0].address == "3401 W. Foster Ave., Chicago"
    assert records[0].source == "cdp"


def test_ingest_cdp_pois_skips_missing_files(tmp_path: Path) -> None:
    """Empty snapshot dir → empty iterator."""
    assert list(ingest_cdp_pois(tmp_path)) == []


def test_ingest_cdp_pois_handles_missing_ward(tmp_path: Path) -> None:
    """Alderman row with no ward field → name is None (not crash)."""
    (tmp_path / "cdp_alderman_offices.geojson").write_text(json.dumps({
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature",
             "properties": {"alderman": "X", "address": "1 St", "city": "Chicago"},
             "geometry": {"type": "Point", "coordinates": [-87.6, 41.9]}},
        ],
    }))
    records = list(ingest_cdp_pois(tmp_path))
    assert len(records) == 1
    assert records[0].name is None
