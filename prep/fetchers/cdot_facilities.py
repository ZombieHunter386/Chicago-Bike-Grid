# prep/fetchers/cdot_facilities.py
"""Fetch + parse the CDOT bike-facility ArcGIS layers.

These supply the **improve-only override** on top of the Cook County LTS
baseline (design 2026-07-29 §3.3): CDOT's Jan-2025 layer knows about
facilities built after the county's 2023 OSM snapshot, so it can lower a
street's LTS but never raise it.

Two FeatureServer layers, mirroring the prep/fetchers/hin.py ArcGIS pattern:
  - on-street `Bikeway_Network_2024_Final_Public` — facility type in `BIKE_DSPLY`
    (PROTECTED/NEIGHBORHOOD/BUFFERED/BIKE/SHARED).
  - off-street `Trails_Network_2024_11_18` — the whole layer maps to LTS 1, so
    its attributes are not consulted; parsed facilities carry off_street=True.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import requests

from prep.fetchers.base import Fetcher, FetchResult
from prep.fetchers.hin import _esri_to_geojson

ON_STREET_FILENAME = "cdot_on_street.geojson"
OFF_STREET_FILENAME = "cdot_off_street.geojson"


@dataclass(frozen=True)
class CdotFacility:
    """One CDOT bike facility.

    facility_type is the on-street `BIKE_DSPLY` value (or None for off-street
    trails). off_street flags the trail layer, which maps to tier 1 regardless
    of facility_type. geometry is a GeoJSON LineString/MultiLineString dict.
    """

    facility_type: str | None
    geometry: dict  # type: ignore[type-arg]
    off_street: bool = False


class CdotFacilitiesFetcher(Fetcher):
    """Fetch CDOT on-street + off-street bike facilities from ArcGIS REST."""

    name = "cdot_facilities"

    def __init__(
        self,
        on_street_url: str,
        facility_type_field: str,
        trails_url: str,
        timeout: float = 60.0,
    ) -> None:
        self.on_street_url = on_street_url
        self.facility_type_field = facility_type_field
        self.trails_url = trails_url
        self.timeout = timeout

    def fetch(self, cache_dir: Path) -> FetchResult:
        warnings: list[str] = []
        status = "OK"
        on_count = 0
        off_count = 0

        try:
            on_geojson = self._query_to_geojson(self.on_street_url)
            on_count = len(on_geojson["features"])
            (cache_dir / ON_STREET_FILENAME).write_text(json.dumps(on_geojson))
        except Exception as e:  # noqa: BLE001
            warnings.append(f"on-street fetch failed: {e}")
            status = "FAIL"

        try:
            off_geojson = self._query_to_geojson(self.trails_url)
            off_count = len(off_geojson["features"])
            (cache_dir / OFF_STREET_FILENAME).write_text(json.dumps(off_geojson))
        except Exception as e:  # noqa: BLE001
            warnings.append(f"off-street fetch failed: {e}")
            status = "FAIL"

        return FetchResult(
            path=cache_dir,
            record_count=on_count + off_count,
            status=status,
            warnings=warnings,
        )

    def _query_to_geojson(self, base_url: str) -> dict:  # type: ignore[type-arg]
        """Page through the feature service, requesting outSR=4326."""
        page_size = 1000
        offset = 0
        all_features: list[dict] = []  # type: ignore[type-arg]

        while True:
            params: dict[str, str | int] = {
                "where": "1=1",
                "outFields": "*",
                "f": "json",
                "outSR": "4326",
                "returnGeometry": "true",
                "resultOffset": offset,
                "resultRecordCount": page_size,
            }
            resp = requests.get(f"{base_url}/query", params=params, timeout=self.timeout)
            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code} from {base_url}")
            data = resp.json()

            sr = (data.get("spatialReference") or {}).get("wkid")
            if sr is not None and sr not in (4326, 4269):
                raise RuntimeError(
                    f"unexpected spatial reference {sr} from {base_url} "
                    f"(expected 4326). Server may not honor outSR."
                )

            page_features = data.get("features", [])
            if not page_features:
                break
            all_features.extend(page_features)

            if not data.get("exceededTransferLimit"):
                break
            offset += page_size

        return _esri_to_geojson({"features": all_features})


def parse_cdot_facilities(
    on_street_path: Path,
    off_street_path: Path,
    facility_type_field: str,
) -> Iterator[CdotFacility]:
    """Yield a CdotFacility per feature across both written geojson files."""
    on = json.loads(on_street_path.read_text())
    for feat in on.get("features", []):
        geom = feat.get("geometry")
        if not geom:
            continue
        yield CdotFacility(
            facility_type=feat.get("properties", {}).get(facility_type_field),
            geometry=geom,
            off_street=False,
        )

    off = json.loads(off_street_path.read_text())
    for feat in off.get("features", []):
        geom = feat.get("geometry")
        if not geom:
            continue
        yield CdotFacility(facility_type=None, geometry=geom, off_street=True)
