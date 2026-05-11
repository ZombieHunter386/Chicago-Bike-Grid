# prep/fetchers/pois_cdp.py
from __future__ import annotations

import json
from pathlib import Path

from prep.fetchers.base import Fetcher, FetchResult
from prep.fetchers.socrata_geom import extract_point_location
from prep.socrata import SocrataClient


class CdpPoisFetcher(Fetcher):
    """Fetch alderman offices and CPL library branches from Chicago Data Portal.

    These are the POI categories brokenspoke doesn't emit (per spec §3.3).
    """

    name = "cdp_pois"

    def __init__(
        self,
        domain: str,
        alderman_dataset_id: str,
        library_dataset_id: str,
        app_token: str = "",
    ) -> None:
        self.client = SocrataClient(domain=domain, app_token=app_token)
        self.alderman_dataset_id = alderman_dataset_id
        self.library_dataset_id = library_dataset_id

    def fetch(self, cache_dir: Path) -> FetchResult:
        warnings: list[str] = []
        total = 0
        status = "OK"

        try:
            aldr_count = self._fetch_to_geojson(
                self.alderman_dataset_id,
                cache_dir / "cdp_alderman_offices.geojson",
                warnings,
            )
            total += aldr_count
        except Exception as e:  # noqa: BLE001
            warnings.append(f"alderman fetch failed: {e}")
            status = "FAIL"

        try:
            lib_count = self._fetch_to_geojson(
                self.library_dataset_id,
                cache_dir / "cdp_libraries.geojson",
                warnings,
            )
            total += lib_count
        except Exception as e:  # noqa: BLE001
            warnings.append(f"library fetch failed: {e}")
            status = "FAIL"

        return FetchResult(
            path=cache_dir,
            record_count=total,
            status=status if not warnings else ("WARN" if status == "OK" else status),
            warnings=warnings,
        )

    def _fetch_to_geojson(
        self,
        dataset_id: str,
        out_path: Path,
        warnings: list[str],
    ) -> int:
        rows = list(self.client.fetch(dataset_id))
        features = []
        for row in rows:
            geom = extract_point_location(row)
            if not geom:
                warnings.append(f"{dataset_id}: row missing/unparseable location: {row}")
                continue
            row.pop("location", None)
            row.pop("latitude", None)
            row.pop("longitude", None)
            features.append({
                "type": "Feature",
                "properties": row,
                "geometry": geom,
            })
        out_path.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
        return len(features)
