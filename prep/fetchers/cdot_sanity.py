# prep/fetchers/cdot_sanity.py
from __future__ import annotations

import json
from pathlib import Path

from prep.fetchers.base import Fetcher, FetchResult
from prep.fetchers.socrata_geom import extract_geometry
from prep.socrata import SocrataClient


class CdotBikewaysFetcher(Fetcher):
    """Fetch CDOT bike facilities from Chicago Data Portal (Socrata).

    Used for sanity-check only — OSM is source of truth.
    """

    name = "cdot_bike_facilities"

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

        out = cache_dir / "cdot_bikeways.geojson"
        out.write_text(json.dumps({"type": "FeatureCollection", "features": features}))

        return FetchResult(
            path=out,
            record_count=len(features),
            status="WARN" if warnings else "OK",
            warnings=warnings,
        )
