# tests/prep/test_main.py
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import networkx as nx
from shapely.geometry import LineString, mapping

from prep.fetchers.base import FetchResult
from prep.fetchers.cdot_facilities import CdotFacility
from prep.main import PipelineResult, _hin_features_from_geojson, run_pipeline


def _make_graph() -> nx.MultiDiGraph:
    """Two edges sharing node 11 -> 3 nodes, 2 undirected street edges."""
    g = nx.MultiDiGraph()
    g.add_node(10, x=-87.680, y=41.940)
    g.add_node(11, x=-87.670, y=41.940)
    g.add_node(12, x=-87.670, y=41.950)
    g.add_edge(
        10, 11, osmid=111, name="W Foster Ave", highway="residential", length=100.0,
        geometry=LineString([(-87.680, 41.940), (-87.670, 41.940)]),
    )
    g.add_edge(
        11, 12, osmid=222, name="N Hoyne Ave", highway="primary", length=100.0,
        geometry=LineString([(-87.670, 41.940), (-87.670, 41.950)]),
    )
    return g


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
  chicago_speed_limits:
    name: "Chicago Speed Limits"
    type: "socrata"
    domain: "data.cityofchicago.org"
    dataset_id: "test-speed-id"
    refresh_cadence: "monthly"
  cook_lts:
    name: "Cook County LTS 2023"
    type: "arcgis_mapserver_layer"
    layer_url: "https://example.com/DOTH_expanded/MapServer/14"
    refresh_cadence: "annual"
  cdot_bike_network:
    name: "CDOT Bikeway Network"
    type: "arcgis_feature_service"
    on_street_url: "https://example.com/cdot_on"
    facility_type_field: "BIKE_DSPLY"
    refresh_cadence: "quarterly"
  cdot_off_street_trails:
    name: "CDOT Off-Street Trails"
    type: "arcgis_feature_service"
    trails_url: "https://example.com/cdot_off"
    refresh_cadence: "quarterly"
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
target:
  name: "Chicago"
  bbox:
    min_lat: 41.0
    max_lat: 42.0
    min_lng: -88.0
    max_lng: -87.0
"""
    )


@patch("prep.main.parse_cdot_facilities")
@patch("prep.main.parse_cook_lts")
@patch("prep.main.build_graph_from_bbox")
@patch("prep.main.OsmPoisFetcher")
@patch("prep.main.CdotFacilitiesFetcher")
@patch("prep.main.CookLtsFetcher")
@patch("prep.main.SpeedLimitsFetcher")
@patch("prep.main.CdpPoisFetcher")
@patch("prep.main.HinFetcher")
def test_run_pipeline_happy_path_writes_db_and_report(
    mock_hin: MagicMock,
    mock_cdp: MagicMock,
    mock_speed: MagicMock,
    mock_cook_lts: MagicMock,
    mock_cdot_fac: MagicMock,
    mock_osm_pois: MagicMock,
    mock_build_graph: MagicMock,
    mock_parse_cook_lts: MagicMock,
    mock_parse_cdot: MagicMock,
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

    def _ok(records: int) -> FetchResult:
        return FetchResult(path=cache_dir, record_count=records, status="OK", warnings=[])

    mock_hin.return_value.fetch.return_value = _ok(50)
    mock_speed.return_value.fetch.return_value = _ok(200)
    mock_cook_lts.return_value.fetch.return_value = _ok(207_000)
    mock_cdot_fac.return_value.fetch.return_value = _ok(900)
    mock_cdp.return_value.fetch.return_value = _ok(60)
    mock_osm_pois.return_value.fetch.return_value = _ok(20)

    # Graph -> 2 edges (osm ways 111, 222), 3 nodes.
    mock_build_graph.return_value = _make_graph()
    # The county rates way 111 as LTS 1; way 222 is absent from the 2023
    # snapshot and is an arterial (highway=primary) -> road-class fallback 4.
    mock_parse_cook_lts.return_value = {"111": 1}
    # A protected lane on way 222's alignment (built after the 2023 snapshot):
    # the improve-only CDOT override pulls that arterial from LTS 4 down to 1.
    mock_parse_cdot.return_value = [
        CdotFacility(
            facility_type="PROTECTED",
            geometry=mapping(LineString([(-87.670, 41.940), (-87.670, 41.950)])),
            off_street=False,
        ),
    ]

    result = run_pipeline(
        config_path=cfg_path,
        cache_dir=cache_dir,
        db_path=db_path,
        treatments_dir=treatments_dir,
        report_path=tmp_path / "prep_report.md",
    )

    assert isinstance(result, PipelineResult)
    assert result.status == "OK"
    assert db_path.exists()
    assert (tmp_path / "prep_report.md").exists()
    # ClassifyStats must reach the report: 1 of 2 edges matched a county way_id.
    report_md = (tmp_path / "prep_report.md").read_text()
    assert "## LTS way-ID match rate" in report_md
    assert "1 (50.0%)" in report_md
    # The CDOT override improved exactly one edge (way 222's arterial).
    assert "improved by a CDOT facility: 1" in report_md
    assert (db_path.parent / "lts-network.geojson.gz").exists()
    assert (db_path.parent / "lts-network.geojson.gz").stat().st_size > 0

    import sqlite3 as _sql
    conn = _sql.connect(db_path)
    try:
        streets_count = conn.execute("SELECT COUNT(*) FROM streets").fetchone()[0]
        ints_count = conn.execute("SELECT COUNT(*) FROM intersections").fetchone()[0]
        lts_values = [r[0] for r in conn.execute("SELECT lts FROM streets ORDER BY lts")]
        meta_rows = conn.execute("SELECT source FROM meta").fetchall()
        schema_meta = conn.execute("SELECT schema_version FROM schema_meta").fetchall()
    finally:
        conn.close()

    assert streets_count == 2, f"expected 2 streets from graph, got {streets_count}"
    # 2 edges share node 11: 3 unique intersection nodes total.
    assert ints_count == 3, f"expected 3 intersections, got {ints_count}"
    # County LTS 1 on way 111; way 222 (primary, unmatched) -> road-class 4,
    # then improved to 1 by the CDOT protected lane on its alignment.
    assert lts_values == [1, 1], f"expected LTS [1, 1], got {lts_values}"
    meta_sources = {row[0] for row in meta_rows}
    assert "hin" in meta_sources
    assert "chicago_speed_limits" in meta_sources
    assert "cook_lts" in meta_sources
    assert "cdot_facilities" in meta_sources
    assert "mellow" not in meta_sources
    assert "cdp_pois" in meta_sources
    assert "osm_pois" in meta_sources
    assert "brokenspoke" not in meta_sources
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
    mock_osm_pois.return_value.fetch.return_value = FetchResult(
        path=cache_dir, record_count=0, status="OK", warnings=[]
    )

    result = run_pipeline(
        config_path=cfg_path,
        cache_dir=cache_dir,
        db_path=db_path,
        treatments_dir=tmp_path / "treatments",
        report_path=tmp_path / "prep_report.md",
    )

    assert result.status == "FAIL"
    assert db_path.read_bytes() == b"PREVIOUS_DB_CONTENTS"


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
