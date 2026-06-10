"""Mellow Bike Map fetch + parse (Phase 2a).

The live fixture (jeancochrane/mellow-bike-map app/mbm/fixtures/mellowroute.json)
is a Django dumpdata list of mbm.mellowroute records. Each record's `fields` has
`slug`, `name`, `bounding_box` (SRID=4326 POLYGON), `type` (street/route/path),
and `ways` (a list of OSM way-ID strings). There is no per-route LineString —
geometry is the way-ID list, so Mellow attaches to OSM edges by way-ID join.
"""

import json
from pathlib import Path

import responses

from prep.fetchers.mellow import MellowFeature, MellowFetcher, parse_mellow_features

RAW_URL = (
    "https://raw.githubusercontent.com/jeancochrane/mellow-bike-map/"
    "master/app/mbm/fixtures/mellowroute.json"
)


def _fetcher() -> MellowFetcher:
    return MellowFetcher(
        fixtures_repo="jeancochrane/mellow-bike-map",
        fixtures_path="app/mbm/fixtures/",
    )


@responses.activate
def test_mellow_fetcher_writes_fixture(cache_dir: Path, fixtures_dir: Path) -> None:
    payload = json.loads((fixtures_dir / "mellowroute_sample.json").read_text())
    responses.add(responses.GET, RAW_URL, json=payload, status=200)

    result = _fetcher().fetch(cache_dir)

    assert result.status == "OK"
    assert result.record_count == 3
    out = cache_dir / "mellowroute.json"
    assert out.exists()
    assert json.loads(out.read_text())[0]["model"] == "mbm.mellowroute"


@responses.activate
def test_mellow_fetcher_handles_http_error(cache_dir: Path) -> None:
    responses.add(responses.GET, RAW_URL, status=503)

    result = _fetcher().fetch(cache_dir)

    assert result.status == "FAIL"
    assert any("503" in w for w in result.warnings)


def test_parse_mellow_features_yields_three_kinds(fixtures_dir: Path) -> None:
    feats = list(parse_mellow_features(fixtures_dir / "mellowroute_sample.json"))

    assert len(feats) == 3
    assert {f.kind for f in feats} == {"street", "path", "route"}
    # every feature carries non-empty OSM way ids (the match key)
    assert all(isinstance(f, MellowFeature) and f.way_ids for f in feats)


def test_parse_mellow_features_path_way_ids(fixtures_dir: Path) -> None:
    by_slug = {f.slug: f for f in parse_mellow_features(fixtures_dir / "mellowroute_sample.json")}

    path = by_slug["lakefront-trail"]
    assert path.kind == "path"
    assert path.way_ids == frozenset({"23754638", "23810295"})
    assert path.name == "Lakefront Trail"


def test_parse_mellow_features_decodes_json_encoded_ways(fixtures_dir: Path) -> None:
    """Regression: the real GitHub fixture stores `ways` as a JSON-*encoded
    string* (e.g. '["4476714", "4476717"]'), not a native JSON list. The original
    parser iterated the string directly, yielding individual characters ('[',
    '"', '4', ...) instead of way ids — so the full-city Mellow join matched only
    ~15 character fragments and ~95% of streets fell through to tier 3. The
    parser must json.loads the string before building way_ids.
    """
    by_slug = {f.slug: f for f in parse_mellow_features(fixtures_dir / "mellowroute_sample.json")}

    loop = by_slug["loop"]
    # Real way ids, not single characters.
    assert loop.way_ids == frozenset({"4476714", "4476717", "4477283"})
    # No single-character fragments leaked through.
    assert all(len(w) > 1 for w in loop.way_ids)
