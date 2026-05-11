# prep/fetchers/speed_limits.py
"""Fetch Chicago Speed Limit Zones from Chicago Data Portal.

NOTE: as of 2026-05-05 verification (see docs/dataset-ids.md), no usable
tabular speed-limit dataset is published on CDP. The `chicago_speed_limits`
source is intentionally absent from sources.yaml. This fetcher class exists
so the orchestrator import doesn't break and so a future contributor can
revive the source by re-adding a yaml entry once a usable dataset emerges.
The unit test exercises the logic via a mocked Socrata response.
"""
from __future__ import annotations

import json
from pathlib import Path

from prep.fetchers.base import Fetcher, FetchResult
from prep.fetchers.socrata_geom import extract_geometry
from prep.socrata import SocrataClient


class SpeedLimitsFetcher(Fetcher):
    """Fetch Chicago Speed Limit Zones from Chicago Data Portal."""

    name = "chicago_speed_limits"

    def __init__(self, domain: str, dataset_id: str, app_token: str = "") -> None:
        self.client = SocrataClient(domain=domain, app_token=app_token)
        self.dataset_id = dataset_id

    def fetch(self, cache_dir: Path) -> FetchResult:
        warnings: list[str] = []
        try:
            rows = list(self.client.fetch(self.dataset_id))
        except Exception as e:  # noqa: BLE001
            return FetchResult(
                path=cache_dir,
                record_count=0,
                status="FAIL",
                warnings=[f"socrata fetch failed: {e}"],
            )

        features = []
        for row in rows:
            geom = extract_geometry(row)
            if not geom:
                warnings.append(f"row {row.get('objectid')} missing geometry")
                continue
            row.pop("the_geom", None)
            features.append({
                "type": "Feature",
                "properties": row,
                "geometry": geom,
            })

        out = cache_dir / "chicago_speed_limits.geojson"
        out.write_text(json.dumps({"type": "FeatureCollection", "features": features}))

        return FetchResult(
            path=out,
            record_count=len(features),
            status="WARN" if warnings else "OK",
            warnings=warnings,
        )
