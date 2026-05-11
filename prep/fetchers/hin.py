# prep/fetchers/hin.py
from __future__ import annotations

import json
from pathlib import Path

import requests

from prep.fetchers.base import Fetcher, FetchResult


class HinFetcher(Fetcher):
    """Fetch CMAP 2025 SAP HIN segments and intersections from ArcGIS REST."""

    name = "hin"

    def __init__(self, segments_url: str, intersections_url: str, timeout: float = 60.0) -> None:
        self.segments_url = segments_url
        self.intersections_url = intersections_url
        self.timeout = timeout

    def fetch(self, cache_dir: Path) -> FetchResult:
        warnings: list[str] = []
        seg_count = 0
        int_count = 0
        status = "OK"

        try:
            seg_geojson = self._query_to_geojson(self.segments_url)
            seg_count = len(seg_geojson["features"])
            (cache_dir / "hin_segments.geojson").write_text(json.dumps(seg_geojson))
        except Exception as e:  # noqa: BLE001
            warnings.append(f"segments fetch failed: {e}")
            status = "FAIL"

        try:
            int_geojson = self._query_to_geojson(self.intersections_url)
            int_count = len(int_geojson["features"])
            (cache_dir / "hin_intersections.geojson").write_text(json.dumps(int_geojson))
        except Exception as e:  # noqa: BLE001
            warnings.append(f"intersections fetch failed: {e}")
            status = "FAIL"

        return FetchResult(
            path=cache_dir,
            record_count=seg_count + int_count,
            status=status,
            warnings=warnings,
        )

    def _query_to_geojson(self, base_url: str) -> dict:  # type: ignore[type-arg]
        """Page through the feature service until all features are fetched.

        ArcGIS Feature Services typically cap at 1000-2000 features per
        /query call. We loop with `resultOffset` and `resultRecordCount`
        until the server stops returning new features.

        Also: we explicitly request `outSR=4326` but verify in the response
        that the returned spatial reference is 4326 — if not, we raise
        rather than silently treat coords as the wrong CRS.
        """
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
                    f"(expected 4326). Server may not honor outSR — reproject before consuming."
                )

            page_features = data.get("features", [])
            if not page_features:
                break
            all_features.extend(page_features)

            if not data.get("exceededTransferLimit"):
                break
            offset += page_size

        return _esri_to_geojson({"features": all_features})


def _esri_to_geojson(esri: dict) -> dict:  # type: ignore[type-arg]
    """Convert an Esri JSON FeatureSet to GeoJSON FeatureCollection."""
    features = []
    for feat in esri.get("features", []):
        attrs = feat.get("attributes", {})
        geom = feat.get("geometry", {})
        gj_geom = _esri_geom_to_geojson(geom)
        if gj_geom is None:
            continue
        features.append({
            "type": "Feature",
            "properties": attrs,
            "geometry": gj_geom,
        })
    return {"type": "FeatureCollection", "features": features}


def _esri_geom_to_geojson(g: dict) -> dict | None:  # type: ignore[type-arg]
    if "x" in g and "y" in g:
        return {"type": "Point", "coordinates": [g["x"], g["y"]]}
    if "paths" in g:
        paths = g["paths"]
        if len(paths) == 1:
            return {"type": "LineString", "coordinates": paths[0]}
        return {"type": "MultiLineString", "coordinates": paths}
    if "rings" in g:
        return {"type": "Polygon", "coordinates": g["rings"]}
    return None
