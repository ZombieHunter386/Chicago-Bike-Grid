# prep/fetchers/cook_lts.py
"""Fetch + parse the Cook County Level of Traffic Stress (2023) layer.

Cook County DoTH publishes an LTS 1-4 rating for every roadway segment in the
Chicago metro area, computed with the UMN Accessibility Observatory
methodology over 2023 OSM data. Because it is OSM-derived, each record's
``way_id`` is a real OSM way ID — so downstream matching to our osmnx edges
is an exact way-ID join (design 2026-07-29 §2/§3), and we never need the
geometry: the fetch is attribute-only (way_id, lts), paginated at the
server's maxRecordCount (2000).

Layer: DOTH_expanded/MapServer/14 (see docs/dataset-ids.md).
"""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path

import requests

from prep.fetchers.base import Fetcher, FetchResult

logger = logging.getLogger(__name__)

SNAPSHOT_FILENAME = "cook_lts.json"
PAGE_SIZE = 2000
# Verified 2026-07-29: the layer holds 207,459 records. A big drop on refresh
# means the county changed the layer out from under us -> surface as WARN.
MIN_EXPECTED_RECORDS = 150_000
VALID_LTS = (1, 2, 3, 4)


def _fail(cache_dir: Path, msg: str) -> FetchResult:
    """Build a FAIL FetchResult with a single warning message."""
    return FetchResult(path=cache_dir, record_count=0, status="FAIL", warnings=[msg])


class CookLtsFetcher(Fetcher):
    """Page way_id+lts attributes out of the Cook County LTS MapServer layer."""

    name = "cook_lts"

    def __init__(
        self,
        layer_url: str,
        timeout: float = 60.0,
        page_size: int = PAGE_SIZE,
    ) -> None:
        self.layer_url = layer_url.rstrip("/")
        self.timeout = timeout
        self.page_size = page_size

    def fetch(self, cache_dir: Path) -> FetchResult:
        records: list[dict] = []
        offset = 0
        try:
            while True:
                params: dict[str, str | int] = {
                    "where": "1=1",
                    "outFields": "way_id,lts",
                    "returnGeometry": "false",
                    "resultOffset": offset,
                    "resultRecordCount": self.page_size,
                    "f": "json",
                }
                resp = requests.get(
                    f"{self.layer_url}/query",
                    params=params,
                    timeout=self.timeout,
                )
                if resp.status_code != 200:
                    return _fail(
                        cache_dir, f"HTTP {resp.status_code} from {self.layer_url}"
                    )
                data = resp.json()
                # ArcGIS reports query errors inside a 200 body.
                if "error" in data:
                    return _fail(cache_dir, f"ArcGIS error: {data['error']}")
                features = data.get("features", [])
                records.extend(f.get("attributes", {}) for f in features)
                if not features or not data.get("exceededTransferLimit", False):
                    break
                offset += len(features)
        except Exception as e:  # noqa: BLE001
            return _fail(cache_dir, f"cook_lts fetch failed: {e}")

        out = cache_dir / SNAPSHOT_FILENAME
        out.write_text(json.dumps(records))
        if len(records) < MIN_EXPECTED_RECORDS:
            return FetchResult(
                path=out, record_count=len(records), status="WARN",
                warnings=[
                    f"only {len(records)} records (expected >= {MIN_EXPECTED_RECORDS})"
                ],
            )
        return FetchResult(path=out, record_count=len(records), status="OK")


def parse_cook_lts(path: Path) -> dict[str, int]:
    """Snapshot -> ``way_id (str) -> lts (int 1-4)``, worst (max) LTS on dupes.

    ``way_id`` arrives as an esri double (24072568.0); keys are normalized to
    plain int-strings ("24072568") to match ``OsmEdge.osm_way_ids``. Records
    with a missing way_id or an unparseable/out-of-range ``lts`` are skipped
    with a warning — the classifier's road-class fallback covers those ways.
    """
    records = json.loads(path.read_text())
    way_lts: dict[str, int] = {}
    for rec in records:
        raw_way = rec.get("way_id")
        # way_id arrives as an esri double (24072568.0), but may be missing,
        # NaN, or a non-numeric string. int(float(...)) keeps numeric strings
        # like "24072568.0"; everything unusable is skipped (not fatal).
        try:
            way_num = float(raw_way)
        except (TypeError, ValueError, OverflowError):
            logger.warning("cook_lts: unusable way_id %r", raw_way)
            continue
        if not math.isfinite(way_num):
            logger.warning("cook_lts: non-finite way_id %r", raw_way)
            continue
        key = str(int(way_num))

        raw = rec.get("lts")
        try:
            lts = int(str(raw).strip())
        except (TypeError, ValueError):
            logger.warning("cook_lts: unparseable lts %r (way_id=%r)", raw, raw_way)
            continue
        if lts not in VALID_LTS:
            logger.warning("cook_lts: out-of-range lts %d (way_id=%r)", lts, raw_way)
            continue

        prev = way_lts.get(key)
        if prev is None or lts > prev:
            way_lts[key] = lts
    return way_lts
