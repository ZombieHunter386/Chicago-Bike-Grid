# tests/prep/test_lts_ingest.py
import json
import shutil
from pathlib import Path

import pytest

from prep.lts.ingest import (
    BrokenspokeIngestError,
    IntersectionRecord,
    SegmentRecord,
    ingest_brokenspoke_pois,
    ingest_cdp_pois,
    ingest_segments_from_shapefile,
)

# ---------------------------------------------------------------------------
# Shapefile fixture helper
# ---------------------------------------------------------------------------

def _write_pfb_shapefile(path: Path, rows: list[dict]) -> None:
    """Write a PFB-shaped shapefile fixture for testing.

    Each row dict must have a ``_coords`` key containing a list of (x, y)
    coordinate pairs (in EPSG:32616).  All other keys become attribute columns.
    """
    import geopandas as gpd
    from shapely.geometry import LineString

    geoms = [LineString(r.pop("_coords")) for r in rows]
    gdf = gpd.GeoDataFrame(rows, geometry=geoms, crs="EPSG:32616")
    gdf.to_file(path, driver="ESRI Shapefile")


# ---------------------------------------------------------------------------
# Segment shapefile ingest tests
# ---------------------------------------------------------------------------

def test_ingest_segments_from_shapefile_returns_typed_records(tmp_path: Path) -> None:
    shp_path = tmp_path / "neighborhood_ways.shp"
    _write_pfb_shapefile(shp_path, [
        {
            "ROAD_ID": 999,
            "OSM_ID": 12345,
            "INTERSECTI": 1001,
            "INTERSE_01": 1002,
            "NAME": "W Foster Ave",
            "FT_SEG_STR": 4,
            "TF_SEG_STR": 4,
            "FT_INT_STR": 3,
            "TF_INT_STR": 3,
            "FUNCTIONAL": "primary",
            "SPEED_LIMI": 30,
            # Coords in EPSG:32616 (Chicago-area UTM 16N).
            "_coords": [(440000.0, 4640000.0), (440100.0, 4640000.0)],
        },
    ])

    records = list(ingest_segments_from_shapefile(shp_path))
    assert len(records) == 1
    seg = records[0]
    assert isinstance(seg, SegmentRecord)
    assert seg.road_id == 999
    assert seg.osm_id == 12345
    assert seg.head_int_id == 1001
    assert seg.tail_int_id == 1002
    assert seg.name == "W Foster Ave"
    assert seg.lts == 4
    assert seg.ft_int_str == 3
    assert seg.tf_int_str == 3
    assert seg.geometry_wkt.startswith("LINESTRING")
    # After reproject, coordinates should be WGS84 (lng around -87, lat around 41).
    assert "-87" in seg.geometry_wkt or "-88" in seg.geometry_wkt


def test_ingest_segments_lts_is_max_of_both_directions(tmp_path: Path) -> None:
    """lts field should be max(FT_SEG_STR, TF_SEG_STR)."""
    shp_path = tmp_path / "neighborhood_ways.shp"
    _write_pfb_shapefile(shp_path, [
        {
            "ROAD_ID": 1,
            "OSM_ID": 111,
            "INTERSECTI": 10,
            "INTERSE_01": 11,
            "NAME": "Test St",
            "FT_SEG_STR": 2,
            "TF_SEG_STR": 4,
            "FT_INT_STR": 1,
            "TF_INT_STR": 2,
            "FUNCTIONAL": "residential",
            "SPEED_LIMI": 25,
            "_coords": [(440000.0, 4640000.0), (440100.0, 4640000.0)],
        },
    ])
    records = list(ingest_segments_from_shapefile(shp_path))
    assert records[0].lts == 4


def test_ingest_segments_one_way_nan_tf_direction(tmp_path: Path) -> None:
    """One-way streets have NaN in one direction; record should still be produced."""
    import geopandas as gpd
    from shapely.geometry import LineString

    shp_path = tmp_path / "neighborhood_ways.shp"
    # Build gdf manually to include an actual NaN value.
    geoms = [LineString([(440000.0, 4640000.0), (440100.0, 4640000.0)])]
    gdf = gpd.GeoDataFrame(
        {
            "ROAD_ID": [2],
            "OSM_ID": [222],
            "INTERSECTI": [20],
            "INTERSE_01": [21],
            "NAME": ["One Way St"],
            "FT_SEG_STR": [3],
            "TF_SEG_STR": [float("nan")],
            "FT_INT_STR": [2],
            "TF_INT_STR": [float("nan")],
            "FUNCTIONAL": ["primary"],
            "SPEED_LIMI": [30],
        },
        geometry=geoms,
        crs="EPSG:32616",
    )
    gdf.to_file(shp_path, driver="ESRI Shapefile")

    records = list(ingest_segments_from_shapefile(shp_path))
    assert len(records) == 1
    seg = records[0]
    assert seg.lts == 3
    assert seg.tf_int_str is None


def test_ingest_segments_both_directions_nan_skipped(tmp_path: Path) -> None:
    """Rows where both FT_SEG_STR and TF_SEG_STR are NaN should be skipped."""
    import geopandas as gpd
    from shapely.geometry import LineString

    shp_path = tmp_path / "neighborhood_ways.shp"
    geoms = [LineString([(440000.0, 4640000.0), (440100.0, 4640000.0)])]
    gdf = gpd.GeoDataFrame(
        {
            "ROAD_ID": [3],
            "OSM_ID": [333],
            "INTERSECTI": [30],
            "INTERSE_01": [31],
            "NAME": ["Ghost St"],
            "FT_SEG_STR": [float("nan")],
            "TF_SEG_STR": [float("nan")],
            "FT_INT_STR": [float("nan")],
            "TF_INT_STR": [float("nan")],
            "FUNCTIONAL": ["path"],
            "SPEED_LIMI": [float("nan")],
        },
        geometry=geoms,
        crs="EPSG:32616",
    )
    gdf.to_file(shp_path, driver="ESRI Shapefile")

    records = list(ingest_segments_from_shapefile(shp_path))
    assert records == []


def test_ingest_segments_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(BrokenspokeIngestError):
        list(ingest_segments_from_shapefile(tmp_path / "nonexistent.shp"))


def test_ingest_segments_reprojection_to_wgs84(tmp_path: Path) -> None:
    """Geometry stored in shapefile as EPSG:32616 should be reprojected to EPSG:4326."""
    shp_path = tmp_path / "neighborhood_ways.shp"
    _write_pfb_shapefile(shp_path, [
        {
            "ROAD_ID": 4,
            "OSM_ID": 444,
            "INTERSECTI": 40,
            "INTERSE_01": 41,
            "NAME": "Reproject Test",
            "FT_SEG_STR": 1,
            "TF_SEG_STR": 1,
            "FT_INT_STR": 1,
            "TF_INT_STR": 1,
            "FUNCTIONAL": "residential",
            "SPEED_LIMI": 25,
            "_coords": [(440000.0, 4640000.0), (440100.0, 4640000.0)],
        },
    ])

    records = list(ingest_segments_from_shapefile(shp_path))
    assert len(records) == 1
    wkt = records[0].geometry_wkt
    # WGS84 longitude for Chicago area should be near -87 to -88.
    # The raw EPSG:32616 x value is ~440000, far from -87.
    assert "-87" in wkt or "-88" in wkt
    # Latitude should be around 41-42.
    assert "41." in wkt or "42." in wkt


# ---------------------------------------------------------------------------
# IntersectionRecord dataclass still importable (populated by MVP-B-3)
# ---------------------------------------------------------------------------

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
# POI ingest tests (unchanged — POI ingest from brokenspoke GeoJSON files)
# ---------------------------------------------------------------------------

def test_ingest_brokenspoke_pois_categorizes_by_filename(
    tmp_path: Path, fixtures_dir: Path
) -> None:
    out = tmp_path / "brokenspoke_results"
    out.mkdir()
    # Copy schools fixture as 'neighborhood_schools.geojson'
    shutil.copy(fixtures_dir / "neighborhood_schools_sample.geojson", out / "neighborhood_schools.geojson")
    # Use the same fixture content for hospitals to test category mapping
    shutil.copy(fixtures_dir / "neighborhood_schools_sample.geojson", out / "neighborhood_hospitals.geojson")

    records = list(ingest_brokenspoke_pois(out))

    assert len(records) == 2
    assert {r.category for r in records} == {"school", "hospital"}
    school_rec = next(r for r in records if r.category == "school")
    assert school_rec.name == "Audubon Elementary School"
    assert school_rec.geometry_wkt.startswith("POINT")
    assert school_rec.source == "brokenspoke"


def test_ingest_brokenspoke_pois_skips_unknown_files(tmp_path: Path) -> None:
    out = tmp_path / "brokenspoke_results"
    out.mkdir()
    (out / "neighborhood_unknown_file.geojson").write_text(
        '{"type":"FeatureCollection","features":[]}'
    )
    records = list(ingest_brokenspoke_pois(out))
    assert records == []


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


def test_ingest_brokenspoke_pois_composes_osm_address_from_components(
    tmp_path: Path,
) -> None:
    """OSM POIs typically have addr:housenumber + addr:street + addr:city
    instead of a single addr:full field. Address must be composed from these.
    """
    out = tmp_path / "brokenspoke_results"
    out.mkdir()
    (out / "neighborhood_schools.geojson").write_text(
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
    records = list(ingest_brokenspoke_pois(out))
    addrs = {r.name: r.address for r in records}
    assert addrs["Audubon Elementary"] == "3500 N Hoyne Ave, Chicago"
    assert addrs["Solo Street School"] == "N Lincoln Ave"
    assert addrs["Unknown Address School"] is None
