# Cook County LTS 2023 (4-level) Scoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Mellow+CDOT 3-tier stress classifier with Cook County DoTH's published Level of Traffic Stress (2023) layer, joined to the OSM graph by way ID, and move the whole app (weights, routing personas, UI) to the standard 4-level LTS scale.

**Architecture:** A new ArcGIS attribute-only fetcher pages `way_id`+`lts` out of `DOTH_expanded/MapServer/14`; the classifier becomes a worst-wins way-ID join with a 4-level OSM road-class fallback for unmatched edges. Four routing personas (`kid`/`inexperienced`/`experienced`/`death_wish`) replace `kid`/`parent`/`any`. Everything downstream (graph build, HIN, POIs, exporter, diff) is value-agnostic and needs only comment/UI updates.

**Tech Stack:** Python 3.11, requests, pytest, Flask, MapLibre frontend (vanilla JS). Spec: `docs/specs/2026-07-29-cook-county-lts4-design.md`.

**Conventions:** Run tests with `.venv/bin/python -m pytest` from the repo root (or `make test` for ruff+mypy+pytest). Commit after every green step.

---

## File map

| File | Action |
|---|---|
| `app/core/weights.py` | Rewrite TIERS to 4 personas × 4 LTS levels |
| `prep/config/routing_weights.yaml` | Sync canonical weight tables |
| `tests/app/test_weights.py` | Rewrite for 4×4 |
| `tests/app/test_routing.py`, `test_routes_routing.py`, `test_gap_analysis.py`, `test_routes_gap_analysis.py`, `test_graph.py`, `test_main.py` (app) | Tier-key rename sweep |
| `prep/fetchers/cook_lts.py` | **Create** — fetcher + parser |
| `tests/prep/test_cook_lts_fetcher.py` | **Create** |
| `prep/scoring/classifier.py` | Rewrite — way-ID join + 4-level road-class fallback |
| `tests/prep/test_tier_classifier.py` | Rewrite |
| `prep/scoring/classify_network.py` | Rewrite — new signature + match stats |
| `tests/prep/test_classify_network.py` | Rewrite |
| `prep/reporting/prep_report.py` | Add match-rate section |
| `tests/prep/test_prep_report.py` | Add match-rate test |
| `prep/main.py` | Wire cook_lts in; Mellow/CDOT out |
| `tests/prep/test_main.py` | Update mocks |
| `prep/config/sources.yaml` | Swap source blocks |
| `prep/fetchers/mellow.py`, `prep/fetchers/cdot_facilities.py`, `tests/prep/test_mellow_fetcher.py`, `tests/prep/test_cdot_facilities_fetcher.py` | **Delete** |
| `prep/db/schema.sql`, `app/core/graph.py` | Comment updates (1..3 → 1..4) |
| `app/static/index.html`, `state.js`, `overview.js`, `explore.js`, `explore.html`, `styles.css` | 4 personas + 4-color ramp |
| `README.md`, `docs/dataset-ids.md` | Doc updates |

---

### Task 1: Four-persona weight tables

**Files:**
- Modify: `app/core/weights.py`
- Modify: `prep/config/routing_weights.yaml`
- Test: `tests/app/test_weights.py`

- [ ] **Step 1.1: Rewrite the weights test for 4 personas × 4 LTS levels**

Replace the entire contents of `tests/app/test_weights.py` with:

```python
import pytest

from app.core.weights import INF_WEIGHT, TIERS, fallback_weight_for, main_weight_for


def test_four_tiers_defined() -> None:
    assert set(TIERS.keys()) == {"kid", "inexperienced", "experienced", "death_wish"}


def test_kid_tier_allows_only_lts1() -> None:
    assert main_weight_for("kid", 1) == 1.0
    for lts in (2, 3, 4):
        assert main_weight_for("kid", lts) == INF_WEIGHT


def test_inexperienced_tier_allows_lts2_blocks_3_and_4() -> None:
    assert main_weight_for("inexperienced", 1) == 1.0
    assert main_weight_for("inexperienced", 2) == 1.2
    assert main_weight_for("inexperienced", 3) == INF_WEIGHT
    assert main_weight_for("inexperienced", 4) == INF_WEIGHT


def test_experienced_tier_allows_lts3_blocks_4() -> None:
    assert main_weight_for("experienced", 1) == 1.0
    assert main_weight_for("experienced", 2) == 1.2
    assert main_weight_for("experienced", 3) == 1.5
    assert main_weight_for("experienced", 4) == INF_WEIGHT


def test_death_wish_tier_allows_all_with_graduated_penalty() -> None:
    assert main_weight_for("death_wish", 1) == 1.0
    assert main_weight_for("death_wish", 2) == 1.2
    assert main_weight_for("death_wish", 3) == 1.5
    assert main_weight_for("death_wish", 4) == 2.0


def test_fallback_weights_penalize_out_of_tier_lts() -> None:
    assert fallback_weight_for("kid", 2) == 5.0
    assert fallback_weight_for("kid", 3) == 20.0
    assert fallback_weight_for("kid", 4) == 40.0
    assert fallback_weight_for("inexperienced", 3) == 10.0
    assert fallback_weight_for("inexperienced", 4) == 20.0
    assert fallback_weight_for("experienced", 4) == 10.0
    # death_wish fallback == main (nothing is out of tier)
    assert fallback_weight_for("death_wish", 4) == 2.0


def test_inf_weight_dominates_any_realistic_path_cost() -> None:
    """Routing detects 'no in-tier path' by checking whether any edge in the
    result carries weight >= INF_WEIGHT. INF_WEIGHT must therefore dwarf
    any plausible weighted cost from a finite-weight path (worst case: the
    largest fallback multiplier over Chicago's diameter)."""
    metro_diameter_m = 50_000
    worst_case_cost = metro_diameter_m * 40.0
    assert INF_WEIGHT > worst_case_cost * 100


def test_invalid_lts_raises() -> None:
    with pytest.raises(ValueError):
        main_weight_for("kid", 0)
    with pytest.raises(ValueError):
        main_weight_for("kid", 5)


def test_invalid_tier_raises() -> None:
    with pytest.raises(KeyError):
        main_weight_for("parent", 1)
    with pytest.raises(KeyError):
        main_weight_for("any", 1)
```

- [ ] **Step 1.2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/app/test_weights.py -v`
Expected: FAIL (`test_four_tiers_defined` — old keys `{kid, parent, any}`; LTS-4 lookups raise ValueError).

- [ ] **Step 1.3: Rewrite `app/core/weights.py` tier table**

Replace the `TIERS` dict, `_validate_lts`, and the module docstring's label block. Full new content for those parts (keep `INF_WEIGHT`, `main_weight_for`, `fallback_weight_for` structure as-is):

```python
"""Routing weight tables — single source for spec (2026-07-29 LTS-4) §4.

Tier names map to user-facing labels in the UI:
    "kid"           → "Safe for kid"  (LTS 1 only)
    "inexperienced" → "Inexperienced" (LTS 1-2)
    "experienced"   → "Experienced"   (LTS 1-3)
    "death_wish"    → "Death wish"    (LTS 1-4)

Main weights enforce hard tier cutoffs (∞ for disallowed LTS levels);
fallback weights are applied when the main-weight route returns no path.
Both tables read from this file so values cannot drift between code and
prep/config/routing_weights.yaml (the canonical spec copy).

INF_WEIGHT detection: routing.py checks whether ANY edge in a Dijkstra
result has weight ≥ INF_WEIGHT (rather than thresholding total cost),
which is robust to long-but-legitimate paths whose summed weight could
otherwise approach a chosen threshold.
"""
from __future__ import annotations

INF_WEIGHT = 1e9

# Index i = LTS (i+1). Four entries per table: LTS 1..4.
TIERS: dict[str, dict[str, list[float]]] = {
    "kid": {
        "main":     [1.0, INF_WEIGHT, INF_WEIGHT, INF_WEIGHT],
        "fallback": [1.0, 5.0, 20.0, 40.0],
    },
    "inexperienced": {
        "main":     [1.0, 1.2, INF_WEIGHT, INF_WEIGHT],
        "fallback": [1.0, 1.2, 10.0, 20.0],
    },
    "experienced": {
        "main":     [1.0, 1.2, 1.5, INF_WEIGHT],
        "fallback": [1.0, 1.2, 1.5, 10.0],
    },
    "death_wish": {
        "main":     [1.0, 1.2, 1.5, 2.0],
        "fallback": [1.0, 1.2, 1.5, 2.0],
    },
}


def _validate_lts(lts: int) -> None:
    if lts not in (1, 2, 3, 4):
        raise ValueError(f"lts must be 1..4 (got {lts})")
```

- [ ] **Step 1.4: Run the weights test to verify it passes**

Run: `.venv/bin/python -m pytest tests/app/test_weights.py -v`
Expected: PASS (all).

- [ ] **Step 1.5: Sync `prep/config/routing_weights.yaml`**

Replace the `tiers:` block (keep the header comment style, update the penalty-principle comment):

```yaml
# prep/config/routing_weights.yaml
# CANONICAL routing weights — single source of truth.
# Defined in spec docs/specs/2026-07-29-cook-county-lts4-design.md §4;
# do not change without updating the spec.
#
# Penalty principle: tier controls which LTS levels are allowed; penalty is
# intrinsic to the LTS level. LTS 1 = 1.0×, LTS 2 = 1.2×, LTS 3 = 1.5×,
# LTS 4 = 2.0×. Cook County LTS (2023) publishes the standard 4-level
# Mineta/UMN scale; we use it directly.
# Forbidden = 1e9 (numerical "infinity" stable for graph algorithms).

inf: 1.0e+9  # implementation of "forbidden" / mathematical infinity

tiers:
  kid:
    lts_allowed: [1]
    main:     [1.0, 1.0e+9, 1.0e+9, 1.0e+9]
    fallback: [1.0, 5.0, 20.0, 40.0]

  inexperienced:
    lts_allowed: [1, 2]
    main:     [1.0, 1.2, 1.0e+9, 1.0e+9]
    fallback: [1.0, 1.2, 10.0, 20.0]

  experienced:
    lts_allowed: [1, 2, 3]
    main:     [1.0, 1.2, 1.5, 1.0e+9]
    fallback: [1.0, 1.2, 1.5, 10.0]

  death_wish:
    lts_allowed: [1, 2, 3, 4]
    main:     [1.0, 1.2, 1.5, 2.0]
    fallback: [1.0, 1.2, 1.5, 2.0]
```

- [ ] **Step 1.6: Sweep old tier keys out of app code and tests**

Find every remaining `"parent"` / `"any"` tier reference:

Run: `grep -rn '"parent"\|"any"' app/ tests/app/ --include='*.py'`

Apply this mapping wherever the string is a routing tier key (request payloads, TIERS lookups, expected labels): `"parent"` → `"inexperienced"`, `"any"` → `"death_wish"`. Do NOT touch unrelated uses of the word "any" (e.g. `Any` type hints, prose in comments). Files expected to need edits: `tests/app/test_routing.py`, `tests/app/test_routes_routing.py`, `tests/app/test_gap_analysis.py`, `tests/app/test_routes_gap_analysis.py`, `tests/app/test_graph.py`, `tests/app/test_main.py`, `tests/app/test_smoke_real_db.py` (if it routes). `app/routes/routing.py` and `app/routes/gap_analysis.py` validate via `tier not in TIERS`, so they need no code change — only their tests do.

- [ ] **Step 1.7: Run the app test suite**

Run: `.venv/bin/python -m pytest tests/app -v`
Expected: PASS. (If `test_smoke_real_db.py` skips without a real DB, that's fine — it's marked for real-DB runs.)

- [ ] **Step 1.8: Commit**

```bash
git add app/core/weights.py prep/config/routing_weights.yaml tests/app/
git commit -m "feat(weights): four personas (kid/inexperienced/experienced/death_wish) over LTS 1-4"
```

**Deviation (approved, shipped in the Task 1 commit):** the tier-key sweep surfaced core code the step above did not anticipate — `app/core/gap_analysis.py` holds `_TIER_MAX_LTS`, a direct dict lookup (`_TIER_MAX_LTS[tier]`) that would `KeyError` (500 `/gap-analysis`) under the new tier names. It was updated from `{"kid": 1, "parent": 2, "any": 2}` to `{"kid": 1, "inexperienced": 2, "experienced": 3, "death_wish": 3}`. The top tier `death_wish` (allows LTS 1-4) is capped at 3, not 4, so LTS-4 segments still surface as gap corridors — mirroring the original `any`→2 rationale (a value equal to the tier's max makes the `lts > max` filter always-false). `app/core/gap_analysis.py` and `tests/conftest.py` were added to the `git add` alongside the planned paths. A follow-up commit added a `set(_TIER_MAX_LTS) == set(TIERS)` guard test and a yaml-vs-code drift guard test.

---

### Task 2: Cook County LTS fetcher + parser

**Files:**
- Create: `prep/fetchers/cook_lts.py`
- Test: `tests/prep/test_cook_lts_fetcher.py`

- [ ] **Step 2.1: Write the failing tests**

Create `tests/prep/test_cook_lts_fetcher.py`:

```python
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from prep.fetchers.cook_lts import (
    MIN_EXPECTED_RECORDS,
    SNAPSHOT_FILENAME,
    CookLtsFetcher,
    parse_cook_lts,
)

LAYER_URL = "https://example.com/DOTH_expanded/MapServer/14"


def _page(features: list[dict], exceeded: bool) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "features": [{"attributes": a} for a in features],
        "exceededTransferLimit": exceeded,
    }
    return resp


@patch("prep.fetchers.cook_lts.requests.get")
def test_fetch_paginates_until_transfer_limit_clears(mock_get, tmp_path: Path) -> None:
    page1 = [{"way_id": float(i), "lts": "1"} for i in range(2000)]
    page2 = [{"way_id": 999001.0, "lts": "4"}]
    mock_get.side_effect = [_page(page1, True), _page(page2, False)]

    result = CookLtsFetcher(layer_url=LAYER_URL).fetch(tmp_path)

    assert result.record_count == 2001
    assert mock_get.call_count == 2
    # Second call must advance resultOffset past page 1.
    second_params = mock_get.call_args_list[1].kwargs["params"]
    assert second_params["resultOffset"] == 2000
    assert second_params["returnGeometry"] == "false"
    saved = json.loads((tmp_path / SNAPSHOT_FILENAME).read_text())
    assert len(saved) == 2001


@patch("prep.fetchers.cook_lts.requests.get")
def test_fetch_warns_when_record_count_suspiciously_low(mock_get, tmp_path: Path) -> None:
    mock_get.return_value = _page([{"way_id": 1.0, "lts": "1"}], False)
    result = CookLtsFetcher(layer_url=LAYER_URL).fetch(tmp_path)
    assert result.status == "WARN"
    assert result.record_count == 1
    assert any(str(MIN_EXPECTED_RECORDS) in w for w in result.warnings)


@patch("prep.fetchers.cook_lts.requests.get")
def test_fetch_fails_on_http_error(mock_get, tmp_path: Path) -> None:
    resp = MagicMock()
    resp.status_code = 503
    mock_get.return_value = resp
    result = CookLtsFetcher(layer_url=LAYER_URL).fetch(tmp_path)
    assert result.status == "FAIL"


@patch("prep.fetchers.cook_lts.requests.get")
def test_fetch_fails_on_arcgis_error_payload(mock_get, tmp_path: Path) -> None:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"error": {"code": 400, "message": "bad"}}
    mock_get.return_value = resp
    result = CookLtsFetcher(layer_url=LAYER_URL).fetch(tmp_path)
    assert result.status == "FAIL"


def test_parse_builds_way_lts_map_worst_wins(tmp_path: Path) -> None:
    snapshot = tmp_path / SNAPSHOT_FILENAME
    snapshot.write_text(json.dumps([
        {"way_id": 24072568.0, "lts": "1"},
        {"way_id": 24072568.0, "lts": "3"},   # duplicate way -> worst (3) wins
        {"way_id": 354396977.0, "lts": "4"},
        {"way_id": 111.0, "lts": "garbage"},  # unparseable -> skipped
        {"way_id": 112.0, "lts": "7"},        # out of range -> skipped
        {"way_id": None, "lts": "2"},         # no way id -> skipped
    ]))

    way_lts = parse_cook_lts(snapshot)

    # esri doubles become plain int-strings to match OsmEdge.osm_way_ids.
    assert way_lts == {"24072568": 3, "354396977": 4}
```

- [ ] **Step 2.2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/prep/test_cook_lts_fetcher.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'prep.fetchers.cook_lts'`.

- [ ] **Step 2.3: Implement the fetcher**

Create `prep/fetchers/cook_lts.py`:

```python
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
                resp = requests.get(
                    f"{self.layer_url}/query",
                    params={
                        "where": "1=1",
                        "outFields": "way_id,lts",
                        "returnGeometry": "false",
                        "resultOffset": offset,
                        "resultRecordCount": self.page_size,
                        "f": "json",
                    },
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
```

- [ ] **Step 2.4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/prep/test_cook_lts_fetcher.py -v`
Expected: PASS (6 tests).

- [ ] **Step 2.5: Commit**

```bash
git add prep/fetchers/cook_lts.py tests/prep/test_cook_lts_fetcher.py
git commit -m "feat(prep): Cook County LTS 2023 attribute fetcher + way_id->lts parser"
```

---

### Task 3: Classifier rewrite — way-ID join + 4-level road-class fallback

**Files:**
- Rewrite: `prep/scoring/classifier.py`
- Rewrite test: `tests/prep/test_tier_classifier.py`

- [ ] **Step 3.1: Rewrite the classifier truth-table test**

Replace the entire contents of `tests/prep/test_tier_classifier.py` with:

```python
import pytest

from prep.scoring.classifier import (
    ROAD_CLASS_BASELINE_DEFAULT,
    lts_for_edge,
    road_class_baseline_lts,
)


@pytest.mark.parametrize(
    ("highway", "expected"),
    [
        ("residential", 1), ("living_street", 1), ("cycleway", 1),
        ("path", 1), ("footway", 1), ("pedestrian", 1),
        ("track", 2), ("unclassified", 2), ("tertiary", 2), ("tertiary_link", 2),
        ("secondary", 3), ("secondary_link", 3),
        ("primary", 4), ("primary_link", 4), ("trunk", 4), ("trunk_link", 4),
        ("motorway", 4), ("motorway_link", 4), ("busway", 4),
    ],
)
def test_road_class_baseline_four_levels(highway: str, expected: int) -> None:
    assert road_class_baseline_lts(highway) == expected


def test_road_class_baseline_unknown_or_missing_is_worst_case() -> None:
    assert ROAD_CLASS_BASELINE_DEFAULT == 4
    assert road_class_baseline_lts(None) == 4
    assert road_class_baseline_lts("weird_new_tag") == 4


@pytest.mark.parametrize("lts", [1, 2, 3, 4])
def test_matched_single_way_uses_county_lts(lts: int) -> None:
    result, matched = lts_for_edge(("100",), {"100": lts}, highway="residential")
    assert result == lts
    assert matched is True


def test_multi_way_edge_takes_worst_lts() -> None:
    # A simplified osmnx edge spanning a calm way and a hostile way is as
    # stressful as its worst stretch.
    result, matched = lts_for_edge(
        ("100", "200", "300"),
        {"100": 1, "300": 4},
        highway="residential",
    )
    assert result == 4
    assert matched is True


def test_unmatched_edge_falls_back_to_road_class() -> None:
    result, matched = lts_for_edge(("999",), {"100": 1}, highway="secondary")
    assert result == 3
    assert matched is False


def test_unmatched_edge_with_unknown_highway_is_lts4() -> None:
    result, matched = lts_for_edge(("999",), {}, highway=None)
    assert result == 4
    assert matched is False
```

- [ ] **Step 3.2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/prep/test_tier_classifier.py -v`
Expected: FAIL with ImportError (`lts_for_edge` / `road_class_baseline_lts` don't exist).

- [ ] **Step 3.3: Rewrite `prep/scoring/classifier.py`**

Replace the entire file with:

```python
"""Pure LTS classifier for the Cook County LTS (2023) scoring model.

LTS scale: 1 = least stress, 4 = most (standard Mineta/UMN 4-level scale).
See design docs/specs/2026-07-29-cook-county-lts4-design.md §3.

No geometry / no I/O — pure functions over the way_id->lts map built by
prep.fetchers.cook_lts.parse_cook_lts, so the truth table is exhaustively
unit-testable.
"""

from __future__ import annotations

from collections.abc import Iterable

# Road-class fallback for edges whose OSM way ids don't appear in the 2023
# county snapshot (ways created/renumbered since then). Mirrors what the UMN
# methodology would produce from the road class alone: quiet streets stay
# calm rather than defaulting to worst-case.
ROAD_CLASS_TO_LTS: dict[str, int] = {
    # LTS 1: quiet streets and bike-priority / off-street ways.
    "residential": 1,
    "living_street": 1,
    "cycleway": 1,
    "path": 1,
    "footway": 1,
    "pedestrian": 1,
    # LTS 2: minor through-streets.
    "track": 2,
    "unclassified": 2,
    "tertiary": 2,
    "tertiary_link": 2,
    # LTS 3: secondary arterials.
    "secondary": 3,
    "secondary_link": 3,
    # LTS 4: major arterials and limited-access roads.
    "primary": 4,
    "primary_link": 4,
    "trunk": 4,
    "trunk_link": 4,
    "motorway": 4,
    "motorway_link": 4,
    "busway": 4,
}
# Unknown or missing ``highway`` -> conservative worst case.
ROAD_CLASS_BASELINE_DEFAULT = 4


def road_class_baseline_lts(highway: str | None) -> int:
    """Fallback LTS from the OSM ``highway`` class (None/unknown -> 4)."""
    if not highway:
        return ROAD_CLASS_BASELINE_DEFAULT
    return ROAD_CLASS_TO_LTS.get(highway, ROAD_CLASS_BASELINE_DEFAULT)


def lts_for_edge(
    osm_way_ids: Iterable[str],
    way_lts: dict[str, int],
    highway: str | None,
) -> tuple[int, bool]:
    """Return ``(lts, matched)`` for one OSM edge.

    ``osm_way_ids`` are the edge's OSM way ids (simplified osmnx edges carry
    several); ``way_lts`` is the county way_id->lts map. The edge takes the
    **worst (max)** LTS over its matched ways — a segment is as stressful as
    its worst stretch. When no way matches (2023->now way-id drift), fall
    back to the road-class baseline and report ``matched=False`` so the
    caller can track the match rate.
    """
    worst: int | None = None
    for way_id in osm_way_ids:
        lts = way_lts.get(way_id)
        if lts is not None and (worst is None or lts > worst):
            worst = lts
    if worst is not None:
        return worst, True
    return road_class_baseline_lts(highway), False
```

- [ ] **Step 3.4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/prep/test_tier_classifier.py -v`
Expected: PASS (all parametrized cases).

Note: `tests/prep/test_classify_network.py` now fails to import (classify_network still references deleted names) — that's Task 4's job; don't fix it here.

- [ ] **Step 3.5: Commit**

```bash
git add prep/scoring/classifier.py tests/prep/test_tier_classifier.py
git commit -m "feat(scoring): 4-level LTS classifier — county way-ID join with road-class fallback"
```

---

### Task 4: classify_network rewrite + match stats

**Files:**
- Rewrite: `prep/scoring/classify_network.py`
- Rewrite test: `tests/prep/test_classify_network.py`

- [ ] **Step 4.1: Rewrite the network-classification test**

Replace the entire contents of `tests/prep/test_classify_network.py` with:

```python
from prep.graph.osm_builder import OsmEdge
from prep.scoring.classify_network import ClassifyStats, classify_network


def _edge(road_id: int, way_ids: tuple[str, ...], highway: str) -> OsmEdge:
    return OsmEdge(
        road_id=road_id,
        osm_id=int(way_ids[0]),
        osm_way_ids=way_ids,
        head_node_id=road_id * 10,
        tail_node_id=road_id * 10 + 1,
        name=f"Street {road_id}",
        highway=highway,
        length_m=100.0,
        geometry_wkt="LINESTRING(-87.7 41.9, -87.69 41.9)",
    )


def test_classify_network_joins_by_way_id_and_tracks_match_rate() -> None:
    edges = [
        _edge(1, ("100",), "residential"),        # matched -> LTS 2
        _edge(2, ("200", "300"), "residential"),  # multi-way, worst -> LTS 4
        _edge(3, ("999",), "secondary"),          # unmatched -> road class 3
    ]
    way_lts = {"100": 2, "200": 1, "300": 4}

    records, stats = classify_network(edges, way_lts)

    assert [r.lts for r in records] == [2, 4, 3]
    assert stats == ClassifyStats(matched=2, fallback=1)


def test_classify_stats_match_rate_percent() -> None:
    assert ClassifyStats(matched=3, fallback=1).match_rate_pct == 75.0
    assert ClassifyStats(matched=0, fallback=0).match_rate_pct == 0.0


def test_classify_network_preserves_edge_fields() -> None:
    edges = [_edge(7, ("100",), "residential")]
    records, _ = classify_network(edges, {"100": 1})
    r = records[0]
    assert r.road_id == 7
    assert r.osm_id == 100
    assert r.head_int_id == 70
    assert r.tail_int_id == 71
    assert r.name == "Street 7"
    assert r.highway == "residential"
    assert r.geometry_wkt == "LINESTRING(-87.7 41.9, -87.69 41.9)"
```

- [ ] **Step 4.2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/prep/test_classify_network.py -v`
Expected: FAIL with ImportError (`ClassifyStats` doesn't exist; old signature).

- [ ] **Step 4.3: Rewrite `prep/scoring/classify_network.py`**

Replace the entire file with:

```python
# prep/scoring/classify_network.py
"""Attach a Cook County LTS (1-4) to each OSM edge (design 2026-07-29 §3).

One join: the county layer is OSM-derived, so ``way_id`` matches our edges'
``osm_way_ids`` exactly — a dict lookup, no spatial matching. Unmatched edges
(way-id drift since the 2023 snapshot) fall back to the road-class baseline;
the matched/fallback split is returned so prep_report can publish the match
rate every run.
"""

from __future__ import annotations

from dataclasses import dataclass

from prep.graph.osm_builder import OsmEdge
from prep.lts.ingest import SegmentRecord
from prep.scoring.classifier import lts_for_edge


@dataclass(frozen=True)
class ClassifyStats:
    """How many edges matched a county way_id vs. fell back to road class."""

    matched: int
    fallback: int

    @property
    def match_rate_pct(self) -> float:
        total = self.matched + self.fallback
        return (100.0 * self.matched / total) if total else 0.0


def classify_network(
    edges: list[OsmEdge],
    way_lts: dict[str, int],
) -> tuple[list[SegmentRecord], ClassifyStats]:
    """Classify every OSM edge into a SegmentRecord with its final LTS 1-4."""
    records: list[SegmentRecord] = []
    matched_count = 0
    fallback_count = 0
    for edge in edges:
        lts, matched = lts_for_edge(edge.osm_way_ids, way_lts, edge.highway)
        if matched:
            matched_count += 1
        else:
            fallback_count += 1
        records.append(
            SegmentRecord(
                road_id=edge.road_id,
                osm_id=edge.osm_id,
                head_int_id=edge.head_node_id,
                tail_int_id=edge.tail_node_id,
                name=edge.name,
                lts=lts,
                highway=edge.highway,
                speed=None,
                ft_int_str=None,
                tf_int_str=None,
                geometry_wkt=edge.geometry_wkt,
                raw_properties={},
            )
        )
    return records, ClassifyStats(matched=matched_count, fallback=fallback_count)
```

- [ ] **Step 4.4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/prep/test_classify_network.py tests/prep/test_intersection_tiers.py -v`
Expected: PASS. (`test_intersection_tiers.py` is value-agnostic — the worst-incident rule needs no change for values 1–4.)

- [ ] **Step 4.5: Commit**

```bash
git add prep/scoring/classify_network.py tests/prep/test_classify_network.py
git commit -m "feat(scoring): classify_network via county way-ID join, with match-rate stats"
```

---

### Task 5: Match-rate section in prep_report

**Files:**
- Modify: `prep/reporting/prep_report.py`
- Test: `tests/prep/test_prep_report.py`

- [ ] **Step 5.1: Add the failing test**

Append to `tests/prep/test_prep_report.py` (add the import of `dt`/existing helpers to match the file's existing style — it already builds `SourceRunSummary` lists and calls `build_prep_report`):

```python
def test_report_includes_lts_match_rate_when_provided() -> None:
    report = build_prep_report(
        run_started_at=dt.datetime(2026, 7, 29, 12, 0, tzinfo=dt.UTC),
        run_finished_at=dt.datetime(2026, 7, 29, 12, 5, tzinfo=dt.UTC),
        sources=[],
        lts_matched_edges=9_000,
        lts_fallback_edges=1_000,
    )
    assert "## LTS way-ID match rate" in report
    assert "9000" in report.replace(",", "")
    assert "90.0%" in report


def test_report_omits_match_rate_section_when_absent() -> None:
    report = build_prep_report(
        run_started_at=dt.datetime(2026, 7, 29, 12, 0, tzinfo=dt.UTC),
        run_finished_at=dt.datetime(2026, 7, 29, 12, 5, tzinfo=dt.UTC),
        sources=[],
    )
    assert "match rate" not in report.lower()
```

- [ ] **Step 5.2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/prep/test_prep_report.py -v`
Expected: New tests FAIL (unexpected keyword argument).

- [ ] **Step 5.3: Implement**

In `prep/reporting/prep_report.py`, add two keyword params to `build_prep_report` (after `lts_network_size_bytes`):

```python
    lts_matched_edges: int | None = None,
    lts_fallback_edges: int | None = None,
```

and, after the per-source table block (before the warnings section), insert:

```python
    if lts_matched_edges is not None and lts_fallback_edges is not None:
        total = lts_matched_edges + lts_fallback_edges
        pct = (100.0 * lts_matched_edges / total) if total else 0.0
        lines += [
            "## LTS way-ID match rate",
            "",
            f"- Edges matched to a Cook County way_id: {lts_matched_edges} ({pct:.1f}%)",
            f"- Edges on the road-class fallback: {lts_fallback_edges} ({100 - pct:.1f}%)",
            "",
        ]
```

- [ ] **Step 5.4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/prep/test_prep_report.py -v`
Expected: PASS (all, including pre-existing tests).

- [ ] **Step 5.5: Commit**

```bash
git add prep/reporting/prep_report.py tests/prep/test_prep_report.py
git commit -m "feat(report): LTS way-ID match-rate section in prep_report"
```

---

### Task 6: Pipeline wiring — cook_lts in, Mellow/CDOT out

**Files:**
- Modify: `prep/main.py`
- Modify: `prep/config/sources.yaml`
- Modify: `tests/prep/test_main.py`
- Modify: `prep/db/schema.sql` (comments), `app/core/graph.py` (comments)
- Delete: `prep/fetchers/mellow.py`, `prep/fetchers/cdot_facilities.py`, `tests/prep/test_mellow_fetcher.py`, `tests/prep/test_cdot_facilities_fetcher.py`

- [ ] **Step 6.1: Update `tests/prep/test_main.py` (failing first)**

In the happy-path test and its fixtures:
- In the inline `sources.yaml` fixture string: delete the `mellow:`, `cdot_bike_network:`, and `cdot_off_street_trails:` blocks; add:

```yaml
  cook_lts:
    name: "Cook County LTS 2023"
    type: "arcgis_mapserver_layer"
    layer_url: "https://example.com/DOTH_expanded/MapServer/14"
    refresh_cadence: "annual"
```

- Replace the `@patch("prep.main.MellowFetcher")`, `@patch("prep.main.CdotFacilitiesFetcher")`, `@patch("prep.main.parse_mellow_features")`, `@patch("prep.main.parse_cdot_facilities")` decorators (and their mock args) with:

```python
@patch("prep.main.parse_cook_lts")
@patch("prep.main.CookLtsFetcher")
```

- Set the mocks: `mock_cook_lts.return_value.fetch.return_value = _ok(207_000)` and — replacing the Mellow way-111 setup — `mock_parse_cook_lts.return_value = {"111": 1}` (way 111 → LTS 1; way 222 stays absent so its `highway="primary"` road-class fallback now yields **LTS 4**, not 3 — update that assertion and its comment).
- Update the meta-sources assertions: `"cook_lts" in meta_sources`; remove `"mellow"` / `"cdot_facilities"`.
- Remove the `from prep.fetchers.mellow import MellowFeature` import.

Run: `.venv/bin/python -m pytest tests/prep/test_main.py -v`
Expected: FAIL (`prep.main` has no `CookLtsFetcher`).

- [ ] **Step 6.2: Rewire `prep/main.py`**

- Delete the imports of `cdot_facilities` (`OFF_STREET_FILENAME`, `ON_STREET_FILENAME`, `CdotFacilitiesFetcher`, `parse_cdot_facilities`) and `mellow` (`FIXTURE_FILENAME`, `MellowFetcher`, `parse_mellow_features`).
- Add:

```python
from prep.fetchers.cook_lts import (
    SNAPSHOT_FILENAME as COOK_LTS_FILENAME,
)
from prep.fetchers.cook_lts import (
    CookLtsFetcher,
    parse_cook_lts,
)
```

- In the fetcher section (step 1), replace the `mellow_src` and `cdot_net_src`/`cdot_trails_src` blocks with:

```python
    cook_lts_src = cfg.sources.get("cook_lts")
    if cook_lts_src is not None:
        cook_lts = CookLtsFetcher(layer_url=cook_lts_src.extra["layer_url"])
        r = cook_lts.fetch(snapshot_dir)
        sources.append(SourceRunSummary(
            name="cook_lts", status=r.status, record_count=r.record_count,
            previous_record_count=None, warnings=r.warnings,
        ))
```

- In the build section (step 5), replace the `mellow_features = ...` / `cdot_facilities = ...` assignments and the `classify_network(...)` call with:

```python
        way_lts = (
            parse_cook_lts(snapshot_dir / COOK_LTS_FILENAME)
            if cook_lts_src is not None
            else {}
        )

        segs, classify_stats = classify_network(edges, way_lts)
```

- Thread the stats into the final report call (`build_prep_report(...)` at the end of `run_pipeline`): add

```python
        lts_matched_edges=classify_stats.matched if classify_stats else None,
        lts_fallback_edges=classify_stats.fallback if classify_stats else None,
```

  and initialize `classify_stats = None` before the `try:` block (the build may fail before classification).

- [ ] **Step 6.3: Update `prep/config/sources.yaml`**

Delete the `mellow:`, `cdot_bike_network:`, and `cdot_off_street_trails:` blocks. Add in their place:

```yaml
  cook_lts:
    name: "Cook County Level of Traffic Stress (2023)"
    type: "arcgis_mapserver_layer"
    # Cook County DoTH "DOTH_expanded" service, layer 14 — LTS 1-4 for every
    # roadway segment in the Chicago metro area, UMN Accessibility Observatory
    # methodology over 2023 OSM. Verified 2026-07-29: 207,459 records
    # (LTS 1: 153,880 · 2: 2,858 · 3: 10,985 · 4: 39,736). way_id is a real
    # OSM way id (spot-checked: 24072568 = North Marmora Avenue), so matching
    # is an attribute-only way-ID join — geometry is never fetched.
    # Hub page: https://hub-cookcountyil.opendata.arcgis.com/datasets/cookcountyil::level-of-traffic-stress-2023
    layer_url: "https://gis.cookcountyil.gov/traditional/rest/services/DOTH_expanded/MapServer/14"
    refresh_cadence: "annual"
```

- [ ] **Step 6.4: Delete dead Mellow/CDOT code**

```bash
git rm prep/fetchers/mellow.py prep/fetchers/cdot_facilities.py \
       tests/prep/test_mellow_fetcher.py tests/prep/test_cdot_facilities_fetcher.py
```

Then: `grep -rn "mellow\|cdot_facilities\|CdotFacility\|MellowFeature" prep/ app/ tests/ --include='*.py'` — expected zero hits outside comments; fix any stragglers (e.g. `prep/scoring/__init__.py` re-exports, `config_loader` validation lists if sources are enumerated there — check `prep/config_loader.py`).

- [ ] **Step 6.5: Comment updates (1..3 → 1..4)**

- `prep/db/schema.sql:52` → `lts INTEGER NOT NULL,                -- 1..4`
- `prep/db/schema.sql:72` → `lts_approach INTEGER NOT NULL,       -- 1..4`
- `app/core/graph.py:47` and `:259` → change `values 1..3` to `values 1..4` in both comments.

- [ ] **Step 6.6: Run the prep suite**

Run: `.venv/bin/python -m pytest tests/prep -v`
Expected: PASS (test_main updated, deleted tests gone, everything else untouched).

- [ ] **Step 6.7: Commit**

```bash
git add -A prep/ tests/prep/ app/core/graph.py
git commit -m "feat(prep): wire Cook County LTS into the pipeline; remove Mellow+CDOT sources"
```

---

### Task 7: Frontend — four personas + four-color ramp

**Files:**
- Modify: `app/static/index.html`, `app/static/state.js`, `app/static/overview.js`, `app/static/explore.js`, `app/static/explore.html`, `app/static/styles.css`

Color ramp everywhere: LTS 1 `#16a34a` (green) · LTS 2 `#eab308` (yellow) · LTS 3 `#f59e0b` (orange) · LTS 4 `#dc2626` (red). Unknown stays `#999999`. The HIN overlay magenta `#c026d3` stays; update its "distinct from LTS-3" comment to say LTS-4.

- [ ] **Step 7.1: `index.html` — tier buttons + legend**

Replace the four `#tier-selector` buttons (lines 17–19) with:

```html
      <button data-tier="kid" type="button">Safe for kid <span class="lts-allowance">(LTS 1)</span></button>
      <button data-tier="inexperienced" type="button">Inexperienced <span class="lts-allowance">(LTS 1-2)</span></button>
      <button data-tier="experienced" type="button">Experienced <span class="lts-allowance">(LTS 1-3)</span></button>
      <button data-tier="death_wish" type="button" class="active">Death wish <span class="lts-allowance">(LTS 1-4)</span></button>
```

Replace the legend rows (lines 26–28) with:

```html
      <span class="rl-item"><span class="rl-swatch rl-lts-1"></span>LTS 1 · Calm</span>
      <span class="rl-item"><span class="rl-swatch rl-lts-2"></span>LTS 2 · Moderate</span>
      <span class="rl-item"><span class="rl-swatch rl-lts-3"></span>LTS 3 · Stressful</span>
      <span class="rl-item"><span class="rl-swatch rl-lts-4"></span>LTS 4 · Hostile</span>
```

- [ ] **Step 7.2: `state.js` — default tier + legacy-hash normalization**

Update the shape comment (line 6) to `tier: "kid" | "inexperienced" | "experienced" | "death_wish"`. Change `DEFAULT_STATE` to `tier: "death_wish"`. Add above `loadFromHash`'s use site:

```js
const VALID_TIERS = new Set(["kid", "inexperienced", "experienced", "death_wish"]);
// Shared URLs minted before the 4-level LTS migration used 3 personas.
const LEGACY_TIERS = { parent: "inexperienced", any: "death_wish" };

function normalizeTier(t) {
  if (VALID_TIERS.has(t)) return t;
  return LEGACY_TIERS[t] || "death_wish";
}
```

and change line 66 from `tier: compact.t || "any",` to `tier: normalizeTier(compact.t),`.

- [ ] **Step 7.3: `overview.js` — 4-color route expression**

Replace the `ltsColorExpr` match arms (lines 237–241) with:

```js
    "match", ["get", "lts"],
    1, "#16a34a",   // green — LTS 1 (calm)
    2, "#eab308",   // yellow — LTS 2 (moderate)
    3, "#f59e0b",   // orange — LTS 3 (stressful)
    4, "#dc2626",   // red — LTS 4 (hostile)
    "#999999",      // fallback for unknown LTS values
```

- [ ] **Step 7.4: `explore.js` — 4-color network ramp**

Replace `LTS_COLOR_EXPR` with:

```js
const LTS_COLOR_EXPR = [
  "match",
  ["get", "lts"],
  1, "#16a34a",
  2, "#eab308",
  3, "#f59e0b",
  4, "#dc2626",
  "#999999",
];
```

Update the HIN layer comment from "Distinct from LTS-3 (#dc2626)" to "Distinct from LTS-4 (#dc2626)".

- [ ] **Step 7.5: `explore.html` — legend**

Replace the three legend rows (lines 17–19) with:

```html
      <div class="legend-row"><span class="legend-swatch swatch-lts-1"></span> LTS 1 — Calm</div>
      <div class="legend-row"><span class="legend-swatch swatch-lts-2"></span> LTS 2 — Moderate</div>
      <div class="legend-row"><span class="legend-swatch swatch-lts-3"></span> LTS 3 — Stressful</div>
      <div class="legend-row"><span class="legend-swatch swatch-lts-4"></span> LTS 4 — Hostile</div>
```

- [ ] **Step 7.6: `styles.css` — swatch colors**

Update lines 67–69 and 576–578 to the new ramp and add the fourth swatch in each block:

```css
.rl-swatch.rl-lts-1 { background: #16a34a; }
.rl-swatch.rl-lts-2 { background: #eab308; }
.rl-swatch.rl-lts-3 { background: #f59e0b; }
.rl-swatch.rl-lts-4 { background: #dc2626; }
```

```css
.swatch-lts-1 { background: #16a34a; }
.swatch-lts-2 { background: #eab308; }
.swatch-lts-3 { background: #f59e0b; }
.swatch-lts-4 { background: #dc2626; }
```

- [ ] **Step 7.7: Static-frontend tests + full suite**

Run: `.venv/bin/python -m pytest tests/ -v` (there are static-served-content tests under `tests/static/`/`tests/app/` — fix any that assert on the old button labels or tier keys).
Expected: PASS.

- [ ] **Step 7.8: Verify in the browser**

Start the dev server (`.claude/launch.json` config) and check `/`: four tier buttons render, default is Death wish; `/explore`: legend shows four rows. Routing against the *existing* 3-tier `bikemap.db` still works (values 1–3 are a subset of 1–4) — full visual check with 4-level data happens at rollout.

- [ ] **Step 7.9: Commit**

```bash
git add app/static/
git commit -m "feat(ui): four personas + four-color LTS ramp"
```

---

### Task 8: Docs

**Files:**
- Modify: `README.md`, `docs/dataset-ids.md`

- [ ] **Step 8.1: README**

In `README.md` line 11, replace the parenthetical so setup reads:

```markdown
1. Install Python 3.11+. (No Docker needed — the prep pipeline builds the routing graph from OpenStreetMap via `osmnx` and attaches Cook County's published Level of Traffic Stress (2023) rating, LTS 1-4, to each street by OSM way ID.)
```

Also update the two view descriptions (lines 6–7) if they mention tiers, and mention the four personas in the Advocacy view line.

- [ ] **Step 8.2: dataset-ids.md**

Append a dated section recording the Cook County layer: hub slug `cookcountyil::level-of-traffic-stress-2023`, layer URL `https://gis.cookcountyil.gov/traditional/rest/services/DOTH_expanded/MapServer/14`, fields `way_id` (esri double = OSM way id) + `lts` (string "1"–"4"), 207,459 records with the per-level counts, `maxRecordCount` 2000 + `supportsPagination`, verified 2026-07-29, and the note that the Mellow + CDOT sources it replaces were removed by this plan.

- [ ] **Step 8.3: Commit**

```bash
git add README.md docs/dataset-ids.md
git commit -m "docs: record Cook County LTS 2023 source; update README for LTS-4"
```

---

### Task 9: Full verification

- [ ] **Step 9.1: Whole suite + lint + types**

Run: `make test`
Expected: ruff clean, mypy clean, pytest all green.

- [ ] **Step 9.2: Grep for stragglers**

Run: `grep -rn '"parent"\|"any"\|mellow\|Mellow\|BIKE_DSPLY\|cdot' app/ prep/ tests/ --include='*.py' --include='*.js' --include='*.html' --include='*.yaml'`
Expected: no live-code hits (historical docs/specs/plans are fine and excluded by the globs above).

- [ ] **Step 9.3: Commit any fixes; then done with code**

---

## Rollout (manual, after merge — spec §8)

1. Merge to `main`, push. Railway auto-deploys the app image from the Dockerfile (app deploy is safe ahead of data: LTS 1–3 DB values are a valid subset of 1–4).
2. `make refresh` locally (~30–90 min). Inspect `prep_report.md` — especially the new **LTS way-ID match rate** (expect high 90s%; if it's low, way-id drift is worse than assumed and the road-class fallback is doing too much work — flag before shipping).
3. `make dev`, eyeball `/` (four personas route sensibly; kid routes hug greenways) and `/explore` (4-color ramp; arterials red).
4. Upload data: `SERVICE_URL=<railway app url> UPLOAD_TOKEN=<secret> make upload-db`, then trigger a Railway redeploy so gunicorn reloads the new graph (Railway API token supplied by Hunter 2026-07-29 — use CLI/API, don't commit it).
5. Verify production: route with all four personas, check `/explore` legend + ramp.
