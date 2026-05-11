# prep/fetchers/socrata_geom.py
"""Normalize the wildly inconsistent geometry/location formats Chicago Data
Portal returns across datasets to GeoJSON dicts.

Drop-in replacement for `row.pop("the_geom")` / `row.pop("location")` patterns
in fetchers that need to handle real-world CDP payloads.
"""
from __future__ import annotations

import json
import re
from typing import Any

from shapely import wkt as _wkt
from shapely.geometry import mapping  # noqa: I001

_PAREN_POINT_RE = re.compile(r"^\(\s*([+-]?\d+\.?\d*)\s*,\s*([+-]?\d+\.?\d*)\s*\)$")

_GEOJSON_TYPES = frozenset({
    "Point", "LineString", "Polygon",
    "MultiPoint", "MultiLineString", "MultiPolygon",
    "GeometryCollection",
})


def extract_geometry(row: dict[str, Any]) -> dict | None:  # type: ignore[type-arg]
    """Extract a GeoJSON-shaped geometry from `the_geom` or fall back to lat/lng.

    Returns None if no usable geometry found. The row is NOT mutated.

    Recognized formats:
      - GeoJSON dict: passthrough
      - GeoJSON serialized as JSON string: parsed and returned
      - WKT string (`POINT(...)`, `MULTILINESTRING(...)` etc.): parsed via shapely
      - `_human_address`-style JSON string envelope: NOT geometry -> None
      - Separate `latitude` + `longitude` columns: a Point GeoJSON
    """
    raw = row.get("the_geom")

    if isinstance(raw, dict):
        if raw.get("type") in _GEOJSON_TYPES:
            return raw
        return None

    if isinstance(raw, str):
        s = raw.strip()
        if s.startswith("{"):
            try:
                parsed = json.loads(s)
            except Exception:  # noqa: BLE001
                parsed = None
            if isinstance(parsed, dict) and parsed.get("type") in _GEOJSON_TYPES:
                return parsed
            return None
        try:
            geom = _wkt.loads(s)
            return dict(mapping(geom))
        except Exception:  # noqa: BLE001
            pass

    return extract_point_location(row)


def extract_point_location(row: dict[str, Any]) -> dict | None:  # type: ignore[type-arg]
    """Extract a GeoJSON Point from `location` or `latitude`/`longitude` fields.

    Recognized formats:
      - GeoJSON dict: passthrough
      - Socrata "location" dict with nested latitude/longitude string keys
        (CDP alderman + library datasets)
      - SODA "human" array [lat, lng]: flipped to GeoJSON [lng, lat]
      - "(lat, lng)" paren string: parsed and flipped
      - separate latitude+longitude columns: combined into Point
    """
    raw = row.get("location")

    if isinstance(raw, dict) and raw.get("type") == "Point":
        return raw

    if isinstance(raw, dict) and "latitude" in raw and "longitude" in raw:
        try:
            return {
                "type": "Point",
                "coordinates": [float(raw["longitude"]), float(raw["latitude"])],
            }
        except (TypeError, ValueError):
            return None

    if isinstance(raw, list | tuple) and len(raw) == 2:
        lat, lng = raw
        return {"type": "Point", "coordinates": [float(lng), float(lat)]}

    if isinstance(raw, str):
        m = _PAREN_POINT_RE.match(raw.strip())
        if m:
            lat, lng = m.group(1), m.group(2)
            return {"type": "Point", "coordinates": [float(lng), float(lat)]}
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict) and parsed.get("type") == "Point":
                return parsed
        except Exception:  # noqa: BLE001
            pass

    lat = row.get("latitude")
    lng = row.get("longitude")
    if lat is not None and lng is not None:
        try:
            return {"type": "Point", "coordinates": [float(lng), float(lat)]}
        except (TypeError, ValueError):
            return None

    return None
