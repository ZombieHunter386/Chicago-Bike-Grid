"""Shared record types + POI ingest helpers.

`SegmentRecord` / `IntersectionRecord` are the DB-facing street/intersection
records consumed by `prep.db.builder.DbBuilder`. They are produced by the
scoring layer (`prep.scoring.classify_network` / `prep.scoring.intersection_tiers`)
from the OSM graph (`prep.graph.osm_builder`) — the PFB/brokenspoke ingest that
formerly lived here has been removed (Mellow + CDOT scoring, Phase 5).

`PoiRecord` plus the `ingest_osm_pois` / `ingest_cdp_pois` helpers read the
GeoJSON POI snapshots written by the OSM and CDP fetchers.
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from shapely.geometry import shape


@dataclass(frozen=True)
class SegmentRecord:
    road_id: int             # synthesized stable unique int — HIN match key
    osm_id: int              # single OSM way id (first of the edge's osmid list)
    head_int_id: int         # OSM node id (from-node)
    tail_int_id: int         # OSM node id (to-node)
    name: str | None
    lts: int  # final stress tier (1..3) from Mellow + CDOT
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


@dataclass(frozen=True)
class PoiRecord:
    name: str | None
    category: str
    address: str | None
    geometry_wkt: str
    source: str  # "osm" or "cdp"
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


# Filename → POI category mapping for OSM POI files (written by prep.fetchers.pois_osm).
OSM_POI_FILES: dict[str, str] = {
    "osm_pois_school.geojson": "school",
    "osm_pois_park.geojson": "park",
    "osm_pois_grocery.geojson": "grocery",
    "osm_pois_hospital.geojson": "hospital",
    "osm_pois_transit.geojson": "transit",
    "osm_pois_pharmacy.geojson": "pharmacy",
    "osm_pois_doctor.geojson": "doctor",
    "osm_pois_dentist.geojson": "dentist",
    "osm_pois_university.geojson": "university",
    "osm_pois_college.geojson": "college",
    "osm_pois_community_center.geojson": "community_center",
    "osm_pois_social_services.geojson": "social_services",
    "osm_pois_retail.geojson": "retail",
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
