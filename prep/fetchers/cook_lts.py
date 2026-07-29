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
                    return FetchResult(
                        path=cache_dir, record_count=0, status="FAIL",
                        warnings=[f"HTTP {resp.status_code} from {self.layer_url}"],
                    )
                data = resp.json()
                # ArcGIS reports query errors inside a 200 body.
                if "error" in data:
                    return FetchResult(
                        path=cache_dir, record_count=0, status="FAIL",
                        warnings=[f"ArcGIS error: {data['error']}"],
                    )
                features = data.get("features", [])
                records.extend(f.get("attributes", {}) for f in features)
                if not features or not data.get("exceededTransferLimit", False):
                    break
                offset += len(features)
        except Exception as e:  # noqa: BLE001
            return FetchResult(
                path=cache_dir, record_count=0, status="FAIL",
                warnings=[f"cook_lts fetch failed: {e}"],
            )

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
        way_id = rec.get("way_id")
        raw = rec.get("lts")
        try:
            lts = int(str(raw).strip())
        except (TypeError, ValueError):
            logger.warning("cook_lts: unparseable lts %r (way_id=%r)", raw, way_id)
            continue
        if lts not in VALID_LTS:
            logger.warning("cook_lts: out-of-range lts %d (way_id=%r)", lts, way_id)
            continue
        if way_id is None:
            logger.warning("cook_lts: record with lts=%d has no way_id", lts)
            continue
        key = str(int(way_id))
        prev = way_lts.get(key)
        if prev is None or lts > prev:
            way_lts[key] = lts
    return way_lts
