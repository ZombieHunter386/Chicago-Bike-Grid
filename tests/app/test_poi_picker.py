"""Tests for app.core.poi_picker."""
from pathlib import Path

from app.core.poi_picker import Poi, load_pois, nearest_poi


def test_load_pois_groups_by_category(tiny_bikemap_db_with_pois: Path) -> None:
    pois_by_cat = load_pois(tiny_bikemap_db_with_pois)
    assert "school" in pois_by_cat
    assert "park" in pois_by_cat
    assert "library" in pois_by_cat
    assert len(pois_by_cat["school"]) == 2
    assert len(pois_by_cat["park"]) == 1


def test_nearest_poi_returns_closest_by_crow_flies(tiny_bikemap_db_with_pois: Path) -> None:
    pois_by_cat = load_pois(tiny_bikemap_db_with_pois)
    schools = pois_by_cat["school"]
    # Query near v400 (41.940, -87.670) — Test Elementary is at (41.940, -87.671), closer.
    nearest = nearest_poi(schools, 41.940, -87.670)
    assert nearest is not None
    assert nearest.name == "Test Elementary"


def test_nearest_poi_empty_list_returns_none() -> None:
    assert nearest_poi([], 41.94, -87.67) is None


def test_poi_dataclass_carries_lat_lon(tiny_bikemap_db_with_pois: Path) -> None:
    pois_by_cat = load_pois(tiny_bikemap_db_with_pois)
    p = pois_by_cat["library"][0]
    assert isinstance(p, Poi)
    assert p.lat == 41.940
    assert p.lon == -87.680
    assert p.category == "library"
