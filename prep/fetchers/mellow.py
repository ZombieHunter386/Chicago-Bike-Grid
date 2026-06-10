# prep/fetchers/mellow.py
"""Fetch + parse the Mellow Bike Map route fixtures.

Mellow routes come from the MIT-licensed jeancochrane/mellow-bike-map repo as a
Django dumpdata fixture (`app/mbm/fixtures/mellowroute.json`). Each record is a
`mbm.mellowroute` with a `type` (kind ∈ street/route/path) and a `ways` list of
OSM way-ID strings. There is no per-route LineString geometry, so downstream
matching to OSM edges is a way-ID join (see design §2.1).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import requests

from prep.fetchers.base import Fetcher, FetchResult

FIXTURE_FILENAME = "mellowroute.json"


@dataclass(frozen=True)
class MellowFeature:
    """One Mellow route: a kind and the OSM way ids that comprise it."""

    kind: str  # "street" | "route" | "path"
    way_ids: frozenset[str]
    slug: str
    name: str


class MellowFetcher(Fetcher):
    """Download the Mellow `mellowroute.json` dumpdata fixture from GitHub."""

    name = "mellow"

    def __init__(
        self,
        fixtures_repo: str,
        fixtures_path: str,
        branch: str = "master",
        timeout: float = 60.0,
    ) -> None:
        self.fixtures_repo = fixtures_repo
        self.fixtures_path = fixtures_path.strip("/")
        self.branch = branch
        self.timeout = timeout

    @property
    def raw_url(self) -> str:
        return (
            f"https://raw.githubusercontent.com/{self.fixtures_repo}/"
            f"{self.branch}/{self.fixtures_path}/{FIXTURE_FILENAME}"
        )

    def fetch(self, cache_dir: Path) -> FetchResult:
        try:
            resp = requests.get(self.raw_url, timeout=self.timeout)
            if resp.status_code != 200:
                return FetchResult(
                    path=cache_dir,
                    record_count=0,
                    status="FAIL",
                    warnings=[f"HTTP {resp.status_code} from {self.raw_url}"],
                )
            records = resp.json()
        except Exception as e:  # noqa: BLE001
            return FetchResult(
                path=cache_dir,
                record_count=0,
                status="FAIL",
                warnings=[f"mellow fetch failed: {e}"],
            )

        out = cache_dir / FIXTURE_FILENAME
        out.write_text(json.dumps(records))
        return FetchResult(path=out, record_count=len(records), status="OK")


def parse_mellow_features(path: Path) -> Iterator[MellowFeature]:
    """Yield a MellowFeature per mbm.mellowroute record in the fixture file."""
    records = json.loads(path.read_text())
    for rec in records:
        if rec.get("model") != "mbm.mellowroute":
            continue
        fields = rec.get("fields", {})
        # The real GitHub fixture stores `ways` as a JSON-*encoded string*
        # (e.g. '["4476714", "4476717"]'), not a native list. Decode it first;
        # iterating the raw string would yield single characters, not way ids.
        ways = fields.get("ways", [])
        if isinstance(ways, str):
            ways = json.loads(ways)
        yield MellowFeature(
            kind=fields["type"],
            way_ids=frozenset(str(w) for w in ways),
            slug=fields.get("slug", ""),
            name=fields.get("name", ""),
        )
