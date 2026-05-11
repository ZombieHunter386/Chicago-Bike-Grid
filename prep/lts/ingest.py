"""Ingest PFB brokenspoke-analyzer outputs into typed records.

Field names verified against PFB 2025.01 City Ratings shapefile output
(neighborhood_ways.shp, EPSG:32616).

PFB emits one row per LTS-evaluation block — ROAD_ID is unique per row, but
OSM_ID is many-to-one (one OSM way can produce 100+ PFB rows). Intersection
node IDs are emitted directly in INTERSECTI (from-node) and INTERSE_01
(to-node); we use these as authoritative graph topology rather than
re-deriving them.

Intersection-approach LTS is embedded per-segment in FT_INT_STR / TF_INT_STR.
prep.lts.synthesize_intersections aggregates these per intersection node.
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from shapely.geometry import shape


class BrokenspokeIngestError(Exception):
    pass


# Field name constants — PFB 2025.01 neighborhood_ways.shp verified column names.
SEG_ROAD_ID = "ROAD_ID"          # PFB per-row unique ID — used as streets PK
SEG_OSM_ID = "OSM_ID"
SEG_NAME = "NAME"
SEG_FT_LTS = "FT_SEG_STR"
SEG_TF_LTS = "TF_SEG_STR"
SEG_HIGHWAY = "FUNCTIONAL"
SEG_SPEED = "SPEED_LIMI"
SEG_FT_INT_STR = "FT_INT_STR"    # intersection LTS at the FT (forward) end
SEG_TF_INT_STR = "TF_INT_STR"    # intersection LTS at the TF (reverse) end
SEG_HEAD_INT = "INTERSECTI"      # PFB from-node intersection ID
SEG_TAIL_INT = "INTERSE_01"      # PFB to-node intersection ID


@dataclass(frozen=True)
class SegmentRecord:
    road_id: int             # PFB ROAD_ID — unique per row
    osm_id: int              # PFB OSM_ID — many-to-one (multiple road_ids per OSM way)
    head_int_id: int         # PFB INTERSECTI (from-node)
    tail_int_id: int         # PFB INTERSE_01 (to-node)
    name: str | None
    lts: int  # max(FT_SEG_STR, TF_SEG_STR) — single LTS per edge for v1 routing
    highway: str | None
    speed: int | None
    ft_int_str: int | None   # intersection LTS at the FT (forward) end
    tf_int_str: int | None   # intersection LTS at the TF (reverse) end
    geometry_wkt: str
    raw_properties: dict


@dataclass(frozen=True)
class IntersectionRecord:
    osm_id: int
    lts_approach: int
    signalized: bool | None
    lanes_crossed: int | None
    geometry_wkt: str
    raw_properties: dict


def _is_nan(x: object) -> bool:
    """True if x is NaN. Robust to non-float types (returns False for them)."""
    try:
        import math
        return isinstance(x, float) and math.isnan(x)
    except (TypeError, ValueError):
        return False


def ingest_segments_from_shapefile(path: Path) -> Iterator[SegmentRecord]:
    """Read PFB-format neighborhood_ways shapefile and yield SegmentRecords.

    Reprojects geometry from PFB's EPSG:32616 to EPSG:4326 (storage CRS).
    Skips rows with neither FT_SEG_STR nor TF_SEG_STR (no LTS data).
    Handles NaN per-direction values (one-way streets).
    """
    import geopandas as gpd  # lazy: only imports when used
    from shapely.geometry.base import BaseGeometry

    if not path.exists():
        raise BrokenspokeIngestError(f"missing: {path}")

    gdf = gpd.read_file(path)
    # Reproject to storage CRS (EPSG:4326).
    if gdf.crs is None:
        raise BrokenspokeIngestError(f"shapefile has no CRS: {path}")
    if gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs(epsg=4326)

    for _, row in gdf.iterrows():
        ft = row.get(SEG_FT_LTS)
        tf = row.get(SEG_TF_LTS)
        # NaN handling: pandas/geopandas use float NaN for missing numerics.
        ft_val = None if (ft is None or _is_nan(ft)) else int(ft)
        tf_val = None if (tf is None or _is_nan(tf)) else int(tf)
        if ft_val is None and tf_val is None:
            continue
        # Per-direction max for the single per-edge LTS used in v1 routing.
        lts = max(v for v in (ft_val, tf_val) if v is not None)

        ft_int = row.get(SEG_FT_INT_STR)
        tf_int = row.get(SEG_TF_INT_STR)
        ft_int_val = None if (ft_int is None or _is_nan(ft_int)) else int(ft_int)
        tf_int_val = None if (tf_int is None or _is_nan(tf_int)) else int(tf_int)

        speed_raw = row.get(SEG_SPEED)
        speed_val = None if (speed_raw is None or _is_nan(speed_raw)) else int(speed_raw)

        # PFB intersection node IDs — required (NOT NULL in schema).
        head_int_raw = row.get(SEG_HEAD_INT)
        tail_int_raw = row.get(SEG_TAIL_INT)
        if head_int_raw is None or _is_nan(head_int_raw):
            continue
        if tail_int_raw is None or _is_nan(tail_int_raw):
            continue

        geom: BaseGeometry = row.geometry
        # Drop the geometry from raw_properties; it's not JSON-serializable.
        props = {k: v for k, v in row.to_dict().items() if k != "geometry"}

        yield SegmentRecord(
            road_id=int(row[SEG_ROAD_ID]),
            osm_id=int(row[SEG_OSM_ID]),
            head_int_id=int(head_int_raw),
            tail_int_id=int(tail_int_raw),
            name=row.get(SEG_NAME) or None,
            lts=lts,
            highway=row.get(SEG_HIGHWAY) or None,
            speed=speed_val,
            ft_int_str=ft_int_val,
            tf_int_str=tf_int_val,
            geometry_wkt=geom.wkt,
            raw_properties=props,
        )


def ingest_segments(shp_path: Path) -> Iterator[SegmentRecord]:
    """Read PFB neighborhood_ways shapefile and yield SegmentRecords.

    Thin wrapper around ingest_segments_from_shapefile for API compatibility.
    """
    return ingest_segments_from_shapefile(shp_path)


# Filename → POI category mapping. Update if brokenspoke uses different filenames.
BROKENSPOKE_POI_FILES: dict[str, str] = {
    "neighborhood_schools.geojson": "school",
    "neighborhood_hospitals.geojson": "hospital",
    "neighborhood_parks.geojson": "park",
    "neighborhood_supermarkets.geojson": "grocery",
    "neighborhood_transit.geojson": "transit",
    "neighborhood_pharmacies.geojson": "pharmacy",
    "neighborhood_doctors.geojson": "doctor",
    "neighborhood_dentists.geojson": "dentist",
    "neighborhood_universities.geojson": "university",
    "neighborhood_colleges.geojson": "college",
    "neighborhood_community_centers.geojson": "community_center",
    "neighborhood_social_services.geojson": "social_services",
    "neighborhood_retail.geojson": "retail",
}


@dataclass(frozen=True)
class PoiRecord:
    name: str | None
    category: str
    address: str | None
    geometry_wkt: str
    source: str  # "brokenspoke" or "cdp"
    raw_properties: dict


def _compose_osm_address(props: dict) -> str | None:
    """Build a human-readable address string from OSM address tags.

    OSM uses several conventions:
      - addr:full — single string (rare)
      - addr:housenumber + addr:street + addr:city — most common
      - address — bare 'address' field (some imports)

    Returns the first that yields a non-empty string, else None.
    """
    full = props.get("addr:full") or props.get("address")
    if full:
        return str(full).strip() or None

    housenumber = props.get("addr:housenumber")
    street = props.get("addr:street")
    city = props.get("addr:city")

    if not (housenumber or street):
        return None

    parts = [str(housenumber).strip() if housenumber else None,
             str(street).strip() if street else None]
    line1 = " ".join(p for p in parts if p)
    if not line1:
        return None
    if city:
        return f"{line1}, {str(city).strip()}"
    return line1


def ingest_brokenspoke_pois(results_dir: Path) -> Iterator[PoiRecord]:
    """Walk all known brokenspoke POI files and yield PoiRecords."""
    for filename, category in BROKENSPOKE_POI_FILES.items():
        path = results_dir / filename
        if not path.exists():
            continue
        data = json.loads(path.read_text())
        for feat in data.get("features", []):
            props = feat.get("properties", {})
            geom = shape(feat["geometry"])
            yield PoiRecord(
                name=props.get("name"),
                category=category,
                address=_compose_osm_address(props),
                geometry_wkt=geom.wkt,
                source="brokenspoke",
                raw_properties=props,
            )


# Filename → POI category mapping for OSM POI files (written by prep.fetchers.pois_osm).
OSM_POI_FILES: dict[str, str] = {
    "osm_pois_school.geojson": "school",
    "osm_pois_park.geojson": "park",
    "osm_pois_grocery.geojson": "grocery",
    "osm_pois_hospital.geojson": "hospital",
    "osm_pois_transit.geojson": "transit",
}


def ingest_osm_pois(snapshot_dir: Path) -> Iterator[PoiRecord]:
    """Read osm_pois_<category>.geojson files from a snapshot dir; yield PoiRecords."""
    for filename, category in OSM_POI_FILES.items():
        path = snapshot_dir / filename
        if not path.exists():
            continue
        data = json.loads(path.read_text())
        for feat in data.get("features", []):
            props = feat.get("properties", {})
            geom = shape(feat["geometry"])
            yield PoiRecord(
                name=props.get("name"),
                category=category,
                address=_compose_osm_address(props),
                geometry_wkt=geom.wkt,
                source="osm",
                raw_properties=props,
            )


# CDP POI files written by prep.fetchers.pois_cdp.CdpPoisFetcher.
CDP_POI_FILES: dict[str, str] = {
    "cdp_alderman_offices.geojson": "alderman",
    "cdp_libraries.geojson": "library",
}


def _ordinal(n: int) -> str:
    """1 → '1st', 2 → '2nd', 11 → '11th', 22 → '22nd', etc."""
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _compose_cdp_address(props: dict) -> str | None:
    """Build a human-readable address from CDP's flat fields.

    CDP returns: address, city, state, zipcode (alderman) | zip (library).
    Library uses 'zip', alderman uses 'zipcode'. Try both.
    """
    address = props.get("address")
    if not address:
        return None
    parts = [str(address).strip()]
    city = props.get("city")
    if city:
        parts.append(str(city).strip())
    return ", ".join(parts) if parts else None


def ingest_cdp_pois(snapshot_dir: Path) -> Iterator[PoiRecord]:
    """Read cdp_*.geojson files and yield PoiRecords with source='cdp'.

    Special-cases the per-category name composition:
      - alderman: '34th Ward Alderman Office' (composed from `ward`)
      - library: branch name from the `branch_` field
    """
    for filename, category in CDP_POI_FILES.items():
        path = snapshot_dir / filename
        if not path.exists():
            continue
        data = json.loads(path.read_text())
        for feat in data.get("features", []):
            props = feat.get("properties", {})
            geom = shape(feat["geometry"])
            if category == "alderman":
                ward_raw = props.get("ward")
                try:
                    ward_n = int(ward_raw) if ward_raw is not None else None
                except (TypeError, ValueError):
                    ward_n = None
                name = f"{_ordinal(ward_n)} Ward Alderman Office" if ward_n else None
            elif category == "library":
                name = props.get("branch_") or None
            else:
                name = props.get("name")
            yield PoiRecord(
                name=name,
                category=category,
                address=_compose_cdp_address(props),
                geometry_wkt=geom.wkt,
                source="cdp",
                raw_properties=props,
            )
