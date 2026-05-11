# tests/prep/test_main.py
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import geopandas as gpd
from shapely.geometry import LineString

from prep.fetchers.base import FetchResult
from prep.main import PipelineResult, _hin_features_from_geojson, run_pipeline


def _write_pfb_shapefile(path: Path, rows: list[dict]) -> None:
    """Write a PFB-shaped shapefile fixture for testing."""
    geoms = [LineString(r.pop("_coords")) for r in rows]
    gdf = gpd.GeoDataFrame(rows, geometry=geoms, crs="EPSG:32616")
    gdf.to_file(path, driver="ESRI Shapefile")


def _write_yaml_config(path: Path) -> None:
    path.write_text(
        """
sources:
  hin:
    name: "Test HIN"
    type: "arcgis_feature_service"
    segments_url: "https://example.com/seg"
    intersections_url: "https://example.com/int"
    refresh_cadence: "monthly"
  cdot_bike_facilities:
    name: "CDOT Bike Facilities"
    type: "socrata"
    domain: "data.cityofchicago.org"
    dataset_id: "test-cdot-id"
    refresh_cadence: "monthly"
  chicago_speed_limits:
    name: "Chicago Speed Limits"
    type: "socrata"
    domain: "data.cityofchicago.org"
    dataset_id: "test-speed-id"
    refresh_cadence: "monthly"
  cdp_alderman_offices:
    name: "CDP Alderman Offices"
    type: "socrata"
    domain: "data.cityofchicago.org"
    dataset_id: "test-aldr-id"
    refresh_cadence: "monthly"
  cdp_library_branches:
    name: "CDP Library Branches"
    type: "socrata"
    domain: "data.cityofchicago.org"
    dataset_id: "test-lib-id"
    refresh_cadence: "monthly"
brokenspoke:
  image: "test/img:1.0"
  city_country: "united states"
  city_name: "chicago"
  city_state: "illinois"
  city_fips: "1714000"
  database_url: "postgresql://test"
  network_name: "test_net"
target:
  name: "Chicago"
  bbox:
    min_lat: 41.0
    max_lat: 42.0
    min_lng: -88.0
    max_lng: -87.0
"""
    )


@patch("prep.main.OsmPoisFetcher")
@patch("prep.main.HinFetcher")
@patch("prep.main.CdotBikewaysFetcher")
@patch("prep.main.SpeedLimitsFetcher")
@patch("prep.main.CdpPoisFetcher")
@patch("prep.main.BrokenspokeRunner")
def test_run_pipeline_happy_path_writes_db_and_report(
    mock_bs: MagicMock,
    mock_cdp: MagicMock,
    mock_speed: MagicMock,
    mock_cdot: MagicMock,
    mock_hin: MagicMock,
    mock_osm_pois: MagicMock,
    tmp_path: Path,
    fixtures_dir: Path,
) -> None:
    cfg_path = tmp_path / "sources.yaml"
    _write_yaml_config(cfg_path)
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    db_path = tmp_path / "bikemap.db"
    treatments_dir = tmp_path / "treatments"
    treatments_dir.mkdir()
    results_dir = tmp_path / "brokenspoke_results" / "united-states" / "illinois" / "chicago" / "23.11"
    results_dir.mkdir(parents=True)

    def _ok(records: int) -> FetchResult:
        return FetchResult(path=cache_dir, record_count=records, status="OK", warnings=[])

    mock_hin.return_value.fetch.return_value = _ok(50)
    mock_cdot.return_value.fetch.return_value = _ok(400)
    mock_speed.return_value.fetch.return_value = _ok(200)
    mock_cdp.return_value.fetch.return_value = _ok(60)
    mock_osm_pois.return_value.fetch.return_value = _ok(20)

    mock_bs.return_value.run.return_value = results_dir
    _write_pfb_shapefile(results_dir / "neighborhood_ways.shp", [
        {
            "ROAD_ID": 1,
            "OSM_ID": 12345,
            "INTERSECTI": 100,
            "INTERSE_01": 101,
            "NAME": "W Foster Ave",
            "FT_SEG_STR": 4,
            "TF_SEG_STR": 4,
            "FT_INT_STR": 3,
            "TF_INT_STR": 3,
            "FUNCTIONAL": "primary",
            "SPEED_LIMI": 30,
            "_coords": [(440000.0, 4640000.0), (440100.0, 4640000.0)],
        },
        {
            "ROAD_ID": 2,
            "OSM_ID": 67890,
            "INTERSECTI": 101,             # shared with prior segment's tail
            "INTERSE_01": 102,
            "NAME": "N Hoyne Ave",
            "FT_SEG_STR": 1,
            "TF_SEG_STR": 1,
            "FT_INT_STR": 1,
            "TF_INT_STR": 1,
            "FUNCTIONAL": "residential",
            "SPEED_LIMI": 25,
            "_coords": [(440100.0, 4640000.0), (440100.0, 4640100.0)],
        },
    ])

    result = run_pipeline(
        config_path=cfg_path,
        cache_dir=cache_dir,
        brokenspoke_results_dir=tmp_path / "brokenspoke_results",
        db_path=db_path,
        treatments_dir=treatments_dir,
        report_path=tmp_path / "prep_report.md",
    )

    assert isinstance(result, PipelineResult)
    assert result.status == "OK"
    assert db_path.exists()
    assert (tmp_path / "prep_report.md").exists()
    assert (db_path.parent / "lts-network.geojson.gz").exists()
    assert (db_path.parent / "lts-network.geojson.gz").stat().st_size > 0

    import sqlite3 as _sql
    conn = _sql.connect(db_path)
    try:
        streets_count = conn.execute("SELECT COUNT(*) FROM streets").fetchone()[0]
        ints_count = conn.execute("SELECT COUNT(*) FROM intersections").fetchone()[0]
        meta_rows = conn.execute("SELECT source FROM meta").fetchall()
        schema_meta = conn.execute("SELECT schema_version FROM schema_meta").fetchall()
    finally:
        conn.close()

    assert streets_count == 2, f"expected 2 streets from fixture, got {streets_count}"
    # 2 segments share one endpoint at (440100, 4640000): 3 unique intersection points total.
    assert ints_count == 3, f"expected 3 synthesized intersections, got {ints_count}"
    meta_sources = {row[0] for row in meta_rows}
    assert "hin" in meta_sources
    assert "cdot_bike_facilities" in meta_sources
    assert "chicago_speed_limits" in meta_sources
    assert "cdp_pois" in meta_sources
    assert "brokenspoke" in meta_sources
    assert "osm_pois" in meta_sources
    assert len(schema_meta) == 1, "schema_meta must have exactly one row"


def _write_minimal_yaml_config(path: Path) -> None:
    """Minimal config with only the hin source — used for testing partial/fail scenarios."""
    path.write_text(
        """
sources:
  hin:
    name: "Test HIN"
    type: "arcgis_feature_service"
    segments_url: "https://example.com/seg"
    intersections_url: "https://example.com/int"
    refresh_cadence: "monthly"
brokenspoke:
  image: "test/img:1.0"
  city_country: "united states"
  city_name: "chicago"
  city_state: "illinois"
  city_fips: "1714000"
  database_url: "postgresql://test"
  network_name: "test_net"
target:
  name: "Chicago"
  bbox:
    min_lat: 41.0
    max_lat: 42.0
    min_lng: -88.0
    max_lng: -87.0
"""
    )


@patch("prep.main.OsmPoisFetcher")
@patch("prep.main.HinFetcher")
def test_run_pipeline_failed_source_does_not_overwrite_existing_db(
    mock_hin: MagicMock,
    mock_osm_pois: MagicMock,
    tmp_path: Path,
) -> None:
    cfg_path = tmp_path / "sources.yaml"
    _write_minimal_yaml_config(cfg_path)
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    db_path = tmp_path / "bikemap.db"
    db_path.write_bytes(b"PREVIOUS_DB_CONTENTS")

    mock_hin.return_value.fetch.return_value = FetchResult(
        path=cache_dir, record_count=0, status="FAIL", warnings=["http 503"]
    )

    result = run_pipeline(
        config_path=cfg_path,
        cache_dir=cache_dir,
        brokenspoke_results_dir=tmp_path / "brokenspoke_results",
        db_path=db_path,
        treatments_dir=tmp_path / "treatments",
        report_path=tmp_path / "prep_report.md",
        skip_brokenspoke=True,
    )

    assert result.status == "FAIL"
    assert db_path.read_bytes() == b"PREVIOUS_DB_CONTENTS"


@patch("prep.main.HinFetcher")
@patch("prep.main.CdotBikewaysFetcher")
@patch("prep.main.SpeedLimitsFetcher")
@patch("prep.main.CdpPoisFetcher")
@patch("prep.main.OsmPoisFetcher")
def test_run_pipeline_consumes_preexisting_pfb_results_when_brokenspoke_skipped(
    mock_osm: MagicMock,
    mock_cdp: MagicMock,
    mock_speed: MagicMock,
    mock_cdot: MagicMock,
    mock_hin: MagicMock,
    tmp_path: Path,
) -> None:
    """When skip_brokenspoke=True, the orchestrator should resolve and ingest
    a pre-existing PFB results directory at the conventional path."""
    cfg_path = tmp_path / "sources.yaml"
    _write_yaml_config(cfg_path)
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    db_path = tmp_path / "bikemap.db"
    treatments_dir = tmp_path / "treatments"
    treatments_dir.mkdir()

    # Mimic PFB drop: place the shapefile at the conventional location.
    results_dir = tmp_path / "brokenspoke_results" / "united-states" / "illinois" / "chicago" / "25.01"
    results_dir.mkdir(parents=True)
    _write_pfb_shapefile(results_dir / "neighborhood_ways.shp", [
        {
            "ROAD_ID": 1, "OSM_ID": 12345,
            "INTERSECTI": 100, "INTERSE_01": 101,
            "NAME": "W Foster Ave",
            "FT_SEG_STR": 4, "TF_SEG_STR": 4,
            "FT_INT_STR": 3, "TF_INT_STR": 3,
            "FUNCTIONAL": "primary", "SPEED_LIMI": 30,
            "_coords": [(440000.0, 4640000.0), (440100.0, 4640000.0)],
        },
    ])

    def _ok(n: int) -> FetchResult:
        return FetchResult(path=cache_dir, record_count=n, status="OK", warnings=[])

    mock_hin.return_value.fetch.return_value = _ok(50)
    mock_cdot.return_value.fetch.return_value = _ok(400)
    mock_speed.return_value.fetch.return_value = _ok(200)
    mock_cdp.return_value.fetch.return_value = _ok(60)
    mock_osm.return_value.fetch.return_value = _ok(20)

    result = run_pipeline(
        config_path=cfg_path,
        cache_dir=cache_dir,
        brokenspoke_results_dir=tmp_path / "brokenspoke_results",
        db_path=db_path,
        treatments_dir=treatments_dir,
        report_path=tmp_path / "prep_report.md",
        skip_brokenspoke=True,
    )

    assert result.status == "OK"
    assert db_path.exists()

    import sqlite3 as _sql
    conn = _sql.connect(db_path)
    try:
        streets = conn.execute("SELECT COUNT(*) FROM streets").fetchone()[0]
        ints = conn.execute("SELECT COUNT(*) FROM intersections").fetchone()[0]
    finally:
        conn.close()
    assert streets == 1
    assert ints == 2  # 2 endpoints from a 1-segment fixture


def test_hin_features_from_geojson_segments_use_cmap_field_names(
    tmp_path: Path,
) -> None:
    """Segment HIN features use CMAP fields: Sum_of_KA_Crashes for severity,
    no modal data available."""
    seg_path = tmp_path / "hin_segments.geojson"
    seg_path.write_text(json.dumps({
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "OBJECTID": 42,
                    "Road_Name": "W Foster Ave",
                    "Sum_of_KA_Crashes": 7,
                    "Sum_of_Fatalities": 1,
                    "HIN_Filter": "Comprehensive & Contextual",
                },
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[-87.689, 41.975], [-87.679, 41.975]],
                },
            },
        ],
    }))

    segs, ints = _hin_features_from_geojson(seg_path, "segment")

    assert len(segs) == 1
    assert ints == []
    seg = segs[0]
    assert seg.feature_id == "42"
    assert seg.severity_rank == 7
    # Segment layer has no modal breakdown — both flags must be False.
    assert seg.modal_flags == {"bike": False, "ped": False}


def test_hin_features_from_geojson_intersections_infer_modal_from_pedbike_counts(
    tmp_path: Path,
) -> None:
    """Intersection HIN features: modal flags both True iff PedBike injuries > 0;
    severity_rank from HIN_Intx_CPM_Rank."""
    int_path = tmp_path / "hin_intersections.geojson"
    int_path.write_text(json.dumps({
        "type": "FeatureCollection",
        "features": [
            # Intersection with ped/bike injuries — modal flags should be True.
            {
                "type": "Feature",
                "properties": {
                    "OBJECTID": 100,
                    "PedBike_Fatalities": 1,
                    "PedBike_A_injuries": 0,
                    "HIN_Intx_CPM_Rank": 15,
                },
                "geometry": {"type": "Point", "coordinates": [-87.689, 41.975]},
            },
            # Intersection with motorist-only injuries — modal flags False.
            {
                "type": "Feature",
                "properties": {
                    "OBJECTID": 200,
                    "PedBike_Fatalities": 0,
                    "PedBike_A_injuries": 0,
                    "HIN_Intx_CPM_Rank": 8,
                },
                "geometry": {"type": "Point", "coordinates": [-87.679, 41.975]},
            },
            # PedBike_A_injuries > 0 alone is enough — flags True.
            {
                "type": "Feature",
                "properties": {
                    "OBJECTID": 300,
                    "PedBike_Fatalities": 0,
                    "PedBike_A_injuries": 3,
                    "HIN_Intx_CPM_Rank": 22,
                },
                "geometry": {"type": "Point", "coordinates": [-87.685, 41.972]},
            },
        ],
    }))

    segs, ints = _hin_features_from_geojson(int_path, "intersection")

    assert segs == []
    assert len(ints) == 3
    by_id = {i.feature_id: i for i in ints}
    assert by_id["100"].modal_flags == {"bike": True, "ped": True}
    assert by_id["100"].severity_rank == 15
    assert by_id["200"].modal_flags == {"bike": False, "ped": False}
    assert by_id["200"].severity_rank == 8
    assert by_id["300"].modal_flags == {"bike": True, "ped": True}
    assert by_id["300"].severity_rank == 22
