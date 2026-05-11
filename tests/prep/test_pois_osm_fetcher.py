# tests/prep/test_pois_osm_fetcher.py
import json
from pathlib import Path
from unittest.mock import patch

import geopandas as gpd
from shapely.geometry import Point, Polygon

from prep.fetchers.pois_osm import OsmPoisFetcher

CHICAGO_BBOX = (41.6440, 42.0230, -87.9402, -87.5240)  # min_lat, max_lat, min_lng, max_lng


def test_osm_pois_fetcher_writes_one_file_per_category(tmp_path: Path) -> None:
    """Each category gets its own geojson file in cache_dir."""
    # Mock osmnx to return a stub Point GeoDataFrame for each category.
    fake_gdf = gpd.GeoDataFrame(
        {"name": ["Test"], "amenity": ["school"]},
        geometry=[Point(-87.7, 41.9)],
        crs="EPSG:4326",
    )
    with patch("osmnx.features.features_from_bbox", return_value=fake_gdf):
        fetcher = OsmPoisFetcher(bbox=CHICAGO_BBOX)
        result = fetcher.fetch(tmp_path)

    assert result.status == "OK"
    assert result.record_count > 0
    # All five categories should have written a file.
    for cat in ("school", "park", "grocery", "hospital", "transit"):
        assert (tmp_path / f"osm_pois_{cat}.geojson").exists()


def test_osm_pois_fetcher_filters_small_parks(tmp_path: Path) -> None:
    """Parks under 0.5 acre (2023 m²) should be filtered out."""
    big_park = Polygon([(-87.70, 41.90), (-87.69, 41.90), (-87.69, 41.91), (-87.70, 41.91)])  # ~10000m²
    small_park = Polygon([(-87.70, 41.95), (-87.6999, 41.95), (-87.6999, 41.9501), (-87.70, 41.9501)])  # ~tiny
    parks_gdf = gpd.GeoDataFrame(
        {"name": ["BigPark", "SmallPark"], "leisure": ["park", "park"]},
        geometry=[big_park, small_park],
        crs="EPSG:4326",
    )
    schools_gdf = gpd.GeoDataFrame(
        {"name": [], "amenity": []},
        geometry=[],
        crs="EPSG:4326",
    )

    def mock_features(bbox, tags):  # noqa: ARG001
        if "leisure" in tags:
            return parks_gdf
        return schools_gdf

    with patch("osmnx.features.features_from_bbox", side_effect=mock_features):
        fetcher = OsmPoisFetcher(bbox=CHICAGO_BBOX)
        fetcher.fetch(tmp_path)

    parks_out = json.loads((tmp_path / "osm_pois_park.geojson").read_text())
    park_names = {f["properties"]["name"] for f in parks_out["features"]}
    assert "BigPark" in park_names
    assert "SmallPark" not in park_names


def test_osm_pois_fetcher_centroids_non_point_geometries(tmp_path: Path) -> None:
    """Polygon parks should be collapsed to their centroid Point in output."""
    big_park = Polygon([(-87.70, 41.90), (-87.69, 41.90), (-87.69, 41.91), (-87.70, 41.91)])
    parks_gdf = gpd.GeoDataFrame(
        {"name": ["BigPark"], "leisure": ["park"]},
        geometry=[big_park],
        crs="EPSG:4326",
    )
    empty_gdf = gpd.GeoDataFrame({"name": [], "amenity": []}, geometry=[], crs="EPSG:4326")

    def mock_features(bbox, tags):  # noqa: ARG001
        if "leisure" in tags:
            return parks_gdf
        return empty_gdf

    with patch("osmnx.features.features_from_bbox", side_effect=mock_features):
        fetcher = OsmPoisFetcher(bbox=CHICAGO_BBOX)
        fetcher.fetch(tmp_path)

    parks_out = json.loads((tmp_path / "osm_pois_park.geojson").read_text())
    assert parks_out["features"][0]["geometry"]["type"] == "Point"


def test_osm_pois_fetcher_warns_on_empty_bbox(tmp_path: Path) -> None:
    """If a category returns no features, that's a warning, not a failure."""
    empty_gdf = gpd.GeoDataFrame(
        {"name": [], "amenity": [], "leisure": [], "shop": [], "railway": []},
        geometry=[],
        crs="EPSG:4326",
    )
    with patch("osmnx.features.features_from_bbox", return_value=empty_gdf):
        fetcher = OsmPoisFetcher(bbox=CHICAGO_BBOX)
        result = fetcher.fetch(tmp_path)

    assert result.record_count == 0
    # Still OK status — empty results aren't a failure.
    assert result.status == "OK"
    assert len(result.warnings) == 5  # one warning per category
