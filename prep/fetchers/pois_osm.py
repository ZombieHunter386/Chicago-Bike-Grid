# prep/fetchers/pois_osm.py
from __future__ import annotations

import json
import logging
import signal
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from prep.fetchers.base import Fetcher, FetchResult
from prep.osm_config import configure_osmnx

logger = logging.getLogger(__name__)


_OVERALL_PER_CATEGORY_TIMEOUT_SEC = 180


@contextmanager
def _hard_timeout(seconds: int, message: str):  # type: ignore[no-untyped-def]
    """Backstop timeout via SIGALRM — fires even if osmnx's requests_timeout
    fails to catch a half-closed (CLOSE_WAIT) socket. Unix/main-thread only.
    """
    def _handler(signum, frame):  # type: ignore[no-untyped-def]
        raise TimeoutError(message)
    old_handler = signal.signal(signal.SIGALRM, _handler)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)


class OsmPoisFetcher(Fetcher):
    """Fetch POI categories directly from OpenStreetMap via osmnx.

    Replaces brokenspoke's POI exports (which we don't have for Chicago in
    PFB's pre-computed dataset). Per spec §3.3, OSM is canonical for OSM-
    derivable POIs anyway.
    """

    name = "osm_pois"

    # OSM tag dict per category. Each value can be True (any value) or list of values.
    # The first 5 are the originally-OSM-derived categories; the remaining 8 are
    # the categories brokenspoke formerly repackaged (review F1, user decision
    # 2026-06-09: keep all 13). All come from the same OSM data brokenspoke used.
    CATEGORY_TAGS: dict[str, dict[str, Any]] = {
        "school": {"amenity": "school"},
        "park": {"leisure": "park"},
        "grocery": {"shop": ["supermarket", "grocery"]},
        "hospital": {"amenity": "hospital"},
        "transit": {"railway": "station"},
        "pharmacy": {"amenity": "pharmacy"},
        "doctor": {"amenity": "doctors"},
        "dentist": {"amenity": "dentist"},
        "university": {"amenity": "university"},
        "college": {"amenity": "college"},
        "community_center": {"amenity": "community_centre"},
        "social_services": {"amenity": "social_facility"},
        "retail": {"shop": True},
    }
    # Park area filter (in m²). Spec §3.6 says "≥ 0.5 acre" = 2023 m².
    MIN_PARK_AREA_M2 = 2023.0

    def __init__(self, bbox: tuple[float, float, float, float]) -> None:
        """bbox is (min_lat, max_lat, min_lng, max_lng) — same shape as TargetConfig.bbox."""
        self.bbox = bbox

    def fetch(self, cache_dir: Path) -> FetchResult:
        # NB: lazy import — osmnx is heavyweight; avoid loading on test collection.
        import osmnx as ox

        # Shared cache dir + configurable Overpass endpoint. Defensive HTTP
        # timeout of 120s is belt+suspenders: it catches the common case (slow
        # server) but NOT a CLOSE_WAIT hang where the socket is half-closed and
        # our process never notices — the signal-based alarm in
        # `_fetch_category` covers that backstop.
        #
        # The cache setting must happen here as well as in the graph builder:
        # this fetcher runs *first*, and left unset osmnx defaults to `./cache`
        # at the cwd (the repo root, NOT gitignored).
        configure_osmnx(ox, requests_timeout=120)

        warnings: list[str] = []
        total = 0
        status = "OK"

        for category, tags in self.CATEGORY_TAGS.items():
            out_path = cache_dir / f"osm_pois_{category}.geojson"
            # Resume support: skip categories whose output already exists.
            # Lets a re-run after a partial failure pick up where it stopped.
            if out_path.exists():
                import json
                try:
                    existing = json.loads(out_path.read_text())
                    cached_count = len(existing.get("features", []))
                except Exception:  # noqa: BLE001
                    cached_count = 0
                logger.info("%s: reusing cached file with %d features", category, cached_count)
                total += cached_count
                continue
            try:
                count = self._fetch_category(
                    ox=ox,
                    category=category,
                    tags=tags,
                    out_path=out_path,
                    warnings=warnings,
                )
                total += count
            except Exception as e:  # noqa: BLE001
                warnings.append(f"{category}: fetch failed: {e}")
                status = "WARN"

        return FetchResult(
            path=cache_dir,
            record_count=total,
            status=status,
            warnings=warnings,
        )

    def _fetch_category(
        self,
        ox: Any,
        category: str,
        tags: dict[str, Any],
        out_path: Path,
        warnings: list[str],
    ) -> int:
        """Fetch one category and write features-only GeoJSON. Returns record count."""
        # osmnx 2.x signature: features_from_bbox(bbox=(left, bottom, right, top), tags=...)
        # — west, south, east, north — so build that from our (min_lat, max_lat, min_lng, max_lng).
        min_lat, max_lat, min_lng, max_lng = self.bbox
        bbox_2x = (min_lng, min_lat, max_lng, max_lat)
        with _hard_timeout(
            _OVERALL_PER_CATEGORY_TIMEOUT_SEC,
            f"{category}: hard timeout after {_OVERALL_PER_CATEGORY_TIMEOUT_SEC}s",
        ):
            gdf = ox.features.features_from_bbox(bbox=bbox_2x, tags=tags)

        # Reproject from WGS84 to a metric CRS for area filtering, then back.
        # (osmnx returns EPSG:4326.)
        if category == "park":
            gdf_metric = gdf.to_crs(epsg=6454)  # NAD83(2011) / Illinois East, metres
            gdf = gdf[gdf_metric.geometry.area >= self.MIN_PARK_AREA_M2]

        if len(gdf) == 0:
            logger.warning("%s: 0 features in bbox", category)
            warnings.append(f"{category}: 0 features")
            out_path.write_text(json.dumps({"type": "FeatureCollection", "features": []}))
            return 0

        # Compute centroid for non-Point geometries (parks are polygons; transit may be ways).
        # osmnx returns a mix of Points, LineStrings, Polygons, MultiPolygons.
        # The pois table stores Points only — collapse via centroid.
        gdf = gdf.copy()
        non_point = gdf.geometry.geom_type != "Point"
        if non_point.any():
            # Reproject for accurate centroid, then back.
            gdf.loc[non_point, "geometry"] = (
                gdf.loc[non_point].to_crs(epsg=6454).geometry.centroid.to_crs(epsg=4326)
            )

        # Save as features-only GeoJSON.
        out_path.write_text(gdf.to_json())
        return len(gdf)
