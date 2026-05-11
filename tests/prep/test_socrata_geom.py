# tests/prep/test_socrata_geom.py
import pytest

from prep.fetchers.socrata_geom import (
    extract_geometry,
    extract_point_location,
)


def test_extract_geometry_geojson_dict_passthrough() -> None:
    row = {"the_geom": {"type": "MultiLineString", "coordinates": [[[-87.6, 41.9], [-87.5, 41.9]]]}}
    geom = extract_geometry(row)
    assert geom["type"] == "MultiLineString"
    assert geom["coordinates"][0][0] == [-87.6, 41.9]


def test_extract_geometry_wkt_string_converted() -> None:
    row = {"the_geom": "MULTILINESTRING ((-87.6 41.9, -87.5 41.9))"}
    geom = extract_geometry(row)
    assert geom["type"] == "MultiLineString"
    assert geom["coordinates"][0][0] == pytest.approx([-87.6, 41.9])


def test_extract_geometry_returns_none_when_missing() -> None:
    assert extract_geometry({"name": "X"}) is None


def test_extract_point_location_cdp_dict_with_string_lat_lng() -> None:
    """CDP alderman + library rows have location as a dict with string-typed
    latitude/longitude fields. The extractor must accept that and return a Point.
    """
    row = {
        "location": {
            "latitude": "41.8837645981034",
            "longitude": "-87.63227535353653",
            "human_address": '{"address": "", "city": "", "state": "", "zip": ""}',
        },
    }
    geom = extract_point_location(row)
    assert geom == {
        "type": "Point",
        "coordinates": [pytest.approx(-87.6322753535365), pytest.approx(41.8837645981034)],
    }


def test_extract_geometry_returns_none_for_human_address_object() -> None:
    """_human_address is metadata, not geometry; skip rather than parse."""
    row = {"the_geom": '{"address": "1234 W Foo St", "city": "Chicago"}'}
    assert extract_geometry(row) is None


def test_extract_geometry_json_encoded_geojson_string_parsed() -> None:
    """Some CDP datasets emit GeoJSON serialized as a string."""
    row = {"the_geom": '{"type":"Point","coordinates":[-87.683,41.945]}'}
    geom = extract_geometry(row)
    assert geom == {"type": "Point", "coordinates": [-87.683, 41.945]}


def test_extract_point_location_geojson_dict() -> None:
    row = {"location": {"type": "Point", "coordinates": [-87.683, 41.945]}}
    pt = extract_point_location(row)
    assert pt == {"type": "Point", "coordinates": [-87.683, 41.945]}


def test_extract_point_location_array_lat_lng() -> None:
    """Socrata 'human' format puts lat first, lng second — opposite of GeoJSON."""
    row = {"location": [41.945, -87.683]}
    pt = extract_point_location(row)
    assert pt == {"type": "Point", "coordinates": [-87.683, 41.945]}


def test_extract_point_location_paren_string() -> None:
    row = {"location": "(41.945, -87.683)"}
    pt = extract_point_location(row)
    assert pt == {"type": "Point", "coordinates": [-87.683, 41.945]}


def test_extract_point_location_separate_lat_lng_columns() -> None:
    row = {"latitude": "41.945", "longitude": "-87.683"}
    pt = extract_point_location(row)
    assert pt == {"type": "Point", "coordinates": [-87.683, 41.945]}


def test_extract_point_location_returns_none_when_missing() -> None:
    assert extract_point_location({"name": "X"}) is None
