# Chicago Bike Advocacy Map — Plan 1: Prep Pipeline

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the offline data-prep pipeline that produces `bikemap.db` end-to-end. After this plan lands, `make refresh` runs clean and emits a populated SQLite + SpatiaLite database (streets with LTS, intersections with approach LTS, HIN annotations, POIs, treatments) plus a human-readable `prep_report.md` summarizing the run.

**Architecture:** Python CLI orchestrator + Docker-hosted brokenspoke-analyzer + SQLite/SpatiaLite output. Each data source is a thin fetcher module; brokenspoke produces LTS for both segments and intersections (via the canonical Mineta methodology); a spatial-join module maps CMAP HIN features onto OSM features; a DB builder writes everything into `bikemap.db`. All-or-nothing semantics: any source failure → previous DB untouched.

**Tech Stack:** Python 3.11+, SQLite + SpatiaLite, `requests`, `pyyaml`, `geopandas`, `shapely`, `pyproj`, `pandas`, `python-frontmatter`, `pytest`, `responses`. Docker for brokenspoke-analyzer (`ghcr.io/peopleforbikes/brokenspoke-analyzer:3.1.1`).

**Scope:** This plan covers prep pipeline only. Web service (Plan 2) and frontend (Plan 3) are deferred. Spec reference: `docs/superpowers/specs/2026-05-04-chicago-bike-advocacy-map-design.md`.

**Working state at end of plan:** Running `make refresh` against Chicago produces a valid `chicago-bike-advocacy-map/data/bikemap.db` (~150-250 MB) with all tables populated, plus `prep_report.md` showing per-source OK/WARN/FAIL status and LTS regression vs. last run. `make test` passes clean.

---

## File Structure

```
chicago-bike-advocacy-map/
├── README.md
├── Makefile
├── requirements.txt
├── pyproject.toml             # ruff + mypy config only; deps in requirements.txt
├── .env.example
├── .gitignore
├── prep/
│   ├── __init__.py
│   ├── main.py                # orchestrator entry point
│   ├── config_loader.py       # YAML loader
│   ├── socrata.py             # vendored from chicago-pipeline
│   ├── config/
│   │   ├── sources.yaml
│   │   └── routing_weights.yaml
│   ├── fetchers/
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── hin.py
│   │   ├── cdot_sanity.py
│   │   ├── speed_limits.py
│   │   └── pois_cdp.py
│   ├── lts/
│   │   ├── __init__.py
│   │   ├── runner.py
│   │   └── ingest.py
│   ├── joins/
│   │   ├── __init__.py
│   │   └── hin_to_osm.py
│   ├── db/
│   │   ├── __init__.py
│   │   ├── schema.sql
│   │   ├── builder.py
│   │   └── treatments_loader.py
│   └── reporting/
│       ├── __init__.py
│       ├── lts_diff.py
│       ├── hin_match_report.py
│       └── prep_report.py
├── treatments/
│   ├── pedestrian-refuge.md
│   ├── protected-bike-crossing.md
│   ├── raised-intersection.md
│   ├── neighborhood-greenway.md
│   ├── traffic-circle.md
│   └── photos/
│       └── .gitkeep
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   ├── fixtures/
│   │   ├── hin_sample.geojson
│   │   ├── neighborhood_ways_sample.geojson
│   │   ├── neighborhood_ways_intersections_sample.geojson
│   │   ├── neighborhood_schools_sample.geojson
│   │   ├── cdp_alderman_offices.json
│   │   └── cdp_libraries.json
│   └── prep/
│       ├── __init__.py
│       ├── test_config_loader.py
│       ├── test_fetchers_base.py
│       ├── test_hin_fetcher.py
│       ├── test_cdot_sanity_fetcher.py
│       ├── test_speed_limits_fetcher.py
│       ├── test_pois_cdp_fetcher.py
│       ├── test_lts_runner.py
│       ├── test_lts_ingest.py
│       ├── test_hin_to_osm.py
│       ├── test_db_builder.py
│       ├── test_treatments_loader.py
│       ├── test_lts_diff.py
│       ├── test_hin_match_report.py
│       ├── test_prep_report.py
│       └── test_main.py
├── data/                      # gitignored
│   ├── cache/                 # source caches versioned by date
│   ├── brokenspoke_results/   # brokenspoke output dir
│   └── bikemap.db             # final output (not committed)
└── .github/
    └── workflows/
        └── ci.yml
```

**Responsibility split:**
- `prep/fetchers/` — one module per HTTP source (HIN, CDOT, speed limits, CDP POIs). Each implements `fetch(cache_dir) -> Path` returning the cached file path.
- `prep/lts/runner.py` — wraps the `docker run brokenspoke-analyzer` invocation. Stateless except for output path.
- `prep/lts/ingest.py` — parses `neighborhood_ways.geojson` and `neighborhood_ways_intersections.geojson` from brokenspoke into typed records. Also reads brokenspoke's POI GeoJSON exports.
- `prep/joins/hin_to_osm.py` — pure shapely buffer-and-bearing join.
- `prep/db/builder.py` — single DB write entry point. Builds `bikemap.db` from typed records.
- `prep/reporting/` — read-only consumers that emit `prep_report.md`, `hin_match_report.md`, `lts_diff.md`.
- `prep/main.py` — orchestrator. Reads config, runs fetchers, runs brokenspoke, runs joins, builds DB, emits reports.
- `treatments/` — markdown content (loaded into `treatments` table at prep time).

**Module boundary rule:** `prep/` never imports from `app/` (which doesn't exist yet — Plan 2). `app/` will never import from `prep/` either. Shared utilities, if any, will live in their own minimal module rather than crossing this boundary.

---

## Task 1: Project Scaffolding

**Files:**
- Create: `chicago-bike-advocacy-map/requirements.txt`
- Create: `chicago-bike-advocacy-map/.gitignore`
- Create: `chicago-bike-advocacy-map/.env.example`
- Create: `chicago-bike-advocacy-map/README.md`
- Create: `chicago-bike-advocacy-map/data/.gitkeep`
- Create empty `__init__.py` files in: `prep/`, `prep/fetchers/`, `prep/lts/`, `prep/joins/`, `prep/db/`, `prep/reporting/`, `tests/`, `tests/prep/`

- [ ] **Step 1: Create directory structure**

```bash
mkdir -p chicago-bike-advocacy-map/{prep/{config,fetchers,lts,joins,db,reporting},tests/{prep,fixtures},treatments/photos,data/{cache,brokenspoke_results},.github/workflows}
cd chicago-bike-advocacy-map
touch data/.gitkeep treatments/photos/.gitkeep
touch prep/__init__.py prep/fetchers/__init__.py prep/lts/__init__.py prep/joins/__init__.py prep/db/__init__.py prep/reporting/__init__.py
touch tests/__init__.py tests/prep/__init__.py
```

- [ ] **Step 2: Write requirements.txt**

```
requests==2.32.3
pyyaml==6.0.2
geopandas==1.0.1
shapely==2.0.6
pyproj==3.7.0
pandas==2.2.3
python-frontmatter==1.1.0
python-dotenv==1.0.1
pytest==8.3.3
pytest-cov==5.0.0
responses==0.25.3
ruff==0.7.4
mypy==1.13.0
```

- [ ] **Step 3: Write .gitignore**

```
__pycache__/
*.pyc
.pytest_cache/
.mypy_cache/
.ruff_cache/
.coverage
htmlcov/
.env
.venv/
venv/
.DS_Store
data/cache/
data/brokenspoke_results/
data/bikemap.db
data/bikemap.db-journal
data/cache.db
prep_report.md
hin_match_report.md
lts_diff.md
```

- [ ] **Step 4: Write .env.example**

```
# Optional Socrata app token (Chicago Data Portal). Without it, rate limits are stricter (~1000 req/hour for anonymous).
# Register at https://data.cityofchicago.org for a free token.
SOCRATA_APP_TOKEN=

# Path to bikemap.db (default: data/bikemap.db)
BIKEMAP_DB_PATH=data/bikemap.db
```

- [ ] **Step 5: Write README.md**

```markdown
# Chicago Bike Advocacy Map

A public web tool that turns a Chicago resident's home address and personal destinations into a printable, shareable advocacy artifact showing where bike infrastructure investment would most change their life.

This repository is the **prep pipeline** (Plan 1). The web service and frontend are separate plans.

## Setup

1. Install Python 3.11+ and Docker (Docker required for brokenspoke-analyzer).
2. `python -m venv .venv && source .venv/bin/activate`
3. `pip install -r requirements.txt`
4. Optionally `cp .env.example .env` and fill in `SOCRATA_APP_TOKEN`.

## Usage

```bash
# Run full prep pipeline (~30-90 min for Chicago)
make refresh

# View the run report
make report

# Run tests
make test
```

## Outputs

- `data/bikemap.db` — primary SQLite + SpatiaLite database consumed by the web service.
- `prep_report.md` — per-source OK/WARN/FAIL + record-count deltas + LTS regression diff.
- `hin_match_report.md` — list of HIN features that didn't match any OSM feature.
- `lts_diff.md` — per-segment LTS changes vs. previous prep run.

## Spec

Design spec at `docs/superpowers/specs/2026-05-04-chicago-bike-advocacy-map-design.md`.
```

- [ ] **Step 6: Create venv and install dependencies**

```bash
cd chicago-bike-advocacy-map
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Expected: clean install. (geopandas pulls in fiona, GDAL bindings — may need system GDAL.)

- [ ] **Step 7: Commit**

```bash
git add chicago-bike-advocacy-map/
git commit -m "feat(bikemap): scaffold prep pipeline project"
```

---

## Task 2: Tooling Config (pyproject.toml + conftest.py)

**Files:**
- Create: `chicago-bike-advocacy-map/pyproject.toml`
- Create: `chicago-bike-advocacy-map/tests/conftest.py`

- [ ] **Step 1: Write pyproject.toml (ruff + mypy + pytest config only)**

```toml
[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "W", "I", "N", "UP", "B", "C4", "PIE", "RET", "SIM"]
ignore = ["E501"]  # line-length handled by formatter

[tool.mypy]
python_version = "3.11"
strict = false
ignore_missing_imports = true
disallow_untyped_defs = false
warn_return_any = false

[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = "test_*.py"
addopts = "-v --tb=short"
```

- [ ] **Step 2: Write tests/conftest.py with shared fixtures**

```python
# tests/conftest.py
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    """Path to the test fixtures directory."""
    return FIXTURES


@pytest.fixture
def cache_dir(tmp_path: Path) -> Path:
    """A clean cache directory per test."""
    d = tmp_path / "cache"
    d.mkdir()
    return d


@pytest.fixture
def output_dir(tmp_path: Path) -> Path:
    """A clean output directory per test."""
    d = tmp_path / "out"
    d.mkdir()
    return d
```

- [ ] **Step 3: Verify pytest collects with no tests yet**

Run: `pytest --collect-only`
Expected: `collected 0 items`. No errors.

- [ ] **Step 4: Verify ruff and mypy run without errors on the empty tree**

Run: `ruff check .`
Expected: `All checks passed!` (or similar; no errors)

Run: `mypy prep`
Expected: `Success: no issues found in 0 source files` (or similar)

- [ ] **Step 5: Commit**

```bash
git add chicago-bike-advocacy-map/pyproject.toml chicago-bike-advocacy-map/tests/conftest.py
git commit -m "feat(bikemap): add ruff/mypy/pytest config and shared fixtures"
```

---

## Task 3: Sources YAML + Config Loader (TDD)

**Files:**
- Create: `chicago-bike-advocacy-map/prep/config/sources.yaml`
- Create: `chicago-bike-advocacy-map/prep/config_loader.py`
- Test: `chicago-bike-advocacy-map/tests/prep/test_config_loader.py`

- [ ] **Step 1: Verify Socrata dataset IDs against the live Chicago Data Portal**

The dataset IDs in the YAML below are placeholders. **Verify each by visiting `https://data.cityofchicago.org/d/<id>`** and confirming the dataset name + columns match expectations. If wrong, find the correct ID via search at `https://data.cityofchicago.org/`. Common gotchas: the search returns multiple datasets with similar names (e.g., "Bike Routes" vs "Bike Lanes" vs "Bikeways") — pick the one that includes the protected/buffered facility type, has current `the_geom` data, and shows in CDOT's bike map.

For each of the four Socrata IDs (CDOT bikeways, speed limits, alderman offices, library branches): record the verified ID + dataset URL in `docs/dataset-ids.md`. Update `sources.yaml` if any differ from the placeholders.

```bash
# verification helper — call against each candidate
for ID in 3w5d-sru8 spqx-js37 htai-wnw4 x8fc-8rcq; do
  echo "=== $ID ==="
  curl -sI "https://data.cityofchicago.org/api/views/$ID.json" | head -1
  curl -s "https://data.cityofchicago.org/api/views/$ID.json" | python3 -c "import json,sys; d=json.load(sys.stdin); print('name:', d.get('name')); print('columns:', [c['name'] for c in d.get('columns', [])])"
done
```

If a 200 OK + plausible columns are returned, the ID is correct.

- [ ] **Step 2: Write sources.yaml with verified source URLs**

The source URLs come from §3.3 + §7.1 of the spec. The exact CMAP HIN endpoint is a research item — for now we hard-code the Cook County 2025 SAP HIN ArcGIS REST endpoint and update if it's wrong (verified during Task 26 smoke run).

```yaml
# prep/config/sources.yaml
# Source URLs and metadata for the prep pipeline. Update here, not in code.

sources:
  hin:
    name: "Cook County HIN (2025 SAP)"
    type: "arcgis_feature_service"
    # NOTE: exact REST URL pending §7.1 #1 — placeholder URL below to be verified during Task 26.
    # Reference: https://hub-cookcountyil.opendata.arcgis.com/
    segments_url: "https://services1.arcgis.com/tp9wqSVX1AitKgjd/arcgis/rest/services/CMAP_SAP_HIN_Segments/FeatureServer/0"
    intersections_url: "https://services1.arcgis.com/tp9wqSVX1AitKgjd/arcgis/rest/services/CMAP_SAP_HIN_Intersections/FeatureServer/0"
    refresh_cadence: "monthly"

  cdot_bike_facilities:
    name: "CDOT Bike Facilities (sanity check only)"
    type: "socrata"
    domain: "data.cityofchicago.org"
    dataset_id: "3w5d-sru8"  # Bikeways dataset ID — verify during smoke
    refresh_cadence: "monthly"

  chicago_speed_limits:
    name: "Chicago Speed Limit Zones"
    type: "socrata"
    domain: "data.cityofchicago.org"
    dataset_id: "spqx-js37"  # Speed Limit Zones dataset ID — verify during smoke
    refresh_cadence: "quarterly"

  cdp_alderman_offices:
    name: "Chicago Aldermanic Ward Offices"
    type: "socrata"
    domain: "data.cityofchicago.org"
    dataset_id: "htai-wnw4"  # Ward Offices dataset ID — verify during smoke
    refresh_cadence: "monthly"

  cdp_library_branches:
    name: "Chicago Public Library Branches"
    type: "socrata"
    domain: "data.cityofchicago.org"
    dataset_id: "x8fc-8rcq"  # CPL branches dataset ID — verify during smoke
    refresh_cadence: "monthly"

brokenspoke:
  image: "ghcr.io/peopleforbikes/brokenspoke-analyzer:3.1.1"
  city_country: "united states"
  city_name: "chicago"
  city_state: "illinois"
  city_fips: "1714000"
  database_url: "postgresql://postgres:postgres@postgres:5432/postgres"
  network_name: "bikemap-brokenspoke_default"
  compose_file: "docker/compose.brokenspoke.yml"

target:
  name: "Chicago"
  bbox:
    min_lat: 41.6440
    max_lat: 42.0230
    min_lng: -87.9402
    max_lng: -87.5240
```

- [ ] **Step 3: Write the failing test**

```python
# tests/prep/test_config_loader.py
from pathlib import Path

import pytest

from prep.config_loader import (
    BrokenspokeConfig,
    SourceConfig,
    SourcesFile,
    TargetConfig,
    load_sources_config,
)


def test_load_sources_config_returns_typed_object(tmp_path: Path) -> None:
    yaml_text = """
sources:
  hin:
    name: "Test HIN"
    type: "arcgis_feature_service"
    segments_url: "https://example.com/segments"
    intersections_url: "https://example.com/intersections"
    refresh_cadence: "monthly"
brokenspoke:
  image: "test/img:1.0"
  city_country: "united states"
  city_name: "chicago"
  city_state: "illinois"
  city_fips: "1714000"
  database_url: "postgresql://test"
  network_name: "test_net"
  compose_file: "docker/compose.brokenspoke.yml"
target:
  name: "Test"
  bbox:
    min_lat: 41.0
    max_lat: 42.0
    min_lng: -88.0
    max_lng: -87.0
"""
    cfg_path = tmp_path / "sources.yaml"
    cfg_path.write_text(yaml_text)

    cfg = load_sources_config(cfg_path)

    assert isinstance(cfg, SourcesFile)
    assert "hin" in cfg.sources
    assert cfg.sources["hin"].name == "Test HIN"
    assert cfg.sources["hin"].extra["segments_url"] == "https://example.com/segments"
    assert isinstance(cfg.brokenspoke, BrokenspokeConfig)
    assert cfg.brokenspoke.city_fips == "1714000"
    assert cfg.brokenspoke.compose_file == "docker/compose.brokenspoke.yml"
    assert isinstance(cfg.target, TargetConfig)
    assert cfg.target.bbox == (41.0, 42.0, -88.0, -87.0)


def test_load_sources_config_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_sources_config(tmp_path / "missing.yaml")
```

- [ ] **Step 4: Run test to verify it fails**

Run: `pytest tests/prep/test_config_loader.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'prep.config_loader'`

- [ ] **Step 5: Write the config loader**

```python
# prep/config_loader.py
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class SourceConfig:
    name: str
    type: str
    refresh_cadence: str
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BrokenspokeConfig:
    image: str
    city_country: str
    city_name: str
    city_state: str
    city_fips: str
    database_url: str
    network_name: str
    compose_file: str  # path to docker/compose.brokenspoke.yml (Task 10a)


@dataclass(frozen=True)
class TargetConfig:
    name: str
    bbox: tuple[float, float, float, float]  # (min_lat, max_lat, min_lng, max_lng)


@dataclass(frozen=True)
class SourcesFile:
    sources: dict[str, SourceConfig]
    brokenspoke: BrokenspokeConfig
    target: TargetConfig


def load_sources_config(path: Path) -> SourcesFile:
    """Load and parse sources.yaml into typed config objects."""
    if not path.exists():
        raise FileNotFoundError(f"sources config not found: {path}")
    raw = yaml.safe_load(path.read_text())

    sources: dict[str, SourceConfig] = {}
    for key, src in raw.get("sources", {}).items():
        known_keys = {"name", "type", "refresh_cadence"}
        sources[key] = SourceConfig(
            name=src["name"],
            type=src["type"],
            refresh_cadence=src["refresh_cadence"],
            extra={k: v for k, v in src.items() if k not in known_keys},
        )

    bs = raw["brokenspoke"]
    brokenspoke = BrokenspokeConfig(
        image=bs["image"],
        city_country=bs["city_country"],
        city_name=bs["city_name"],
        city_state=bs["city_state"],
        city_fips=bs["city_fips"],
        database_url=bs["database_url"],
        network_name=bs["network_name"],
        compose_file=bs.get("compose_file", "docker/compose.brokenspoke.yml"),
    )

    tg = raw["target"]
    target = TargetConfig(
        name=tg["name"],
        bbox=(
            float(tg["bbox"]["min_lat"]),
            float(tg["bbox"]["max_lat"]),
            float(tg["bbox"]["min_lng"]),
            float(tg["bbox"]["max_lng"]),
        ),
    )

    return SourcesFile(sources=sources, brokenspoke=brokenspoke, target=target)
```

- [ ] **Step 6: Run test to verify it passes**

Run: `pytest tests/prep/test_config_loader.py -v`
Expected: PASS (2 tests)

- [ ] **Step 7: Commit**

```bash
git add chicago-bike-advocacy-map/prep/config/sources.yaml chicago-bike-advocacy-map/prep/config_loader.py chicago-bike-advocacy-map/tests/prep/test_config_loader.py docs/dataset-ids.md
git commit -m "feat(bikemap): add sources.yaml, typed config loader, verified dataset IDs"
```

---

## Task 4: Routing Weights YAML (Frozen, Sourced from Spec §0.1)

**Files:**
- Create: `chicago-bike-advocacy-map/prep/config/routing_weights.yaml`

This file is the **single source of truth for the LTS weights**. Both the prep pipeline and the (future) web service must read from it; never hardcode the values. The spec §0.1 mandates this discipline to prevent drift.

- [ ] **Step 1: Write routing_weights.yaml**

```yaml
# prep/config/routing_weights.yaml
# CANONICAL routing weights — single source of truth.
# Defined in spec §0.1; do not change without updating the spec.
#
# Penalty principle: tier controls which LTS levels are allowed; penalty is
# intrinsic to the LTS level. LTS 1 = 1.0×, LTS 2 = 1.2×, LTS 3 = 1.5×.
# PFB City Ratings 2025 publishes 1-3 (collapsing original Mineta levels 3+4
# into a single 'high stress' tier); we use that scale directly.
# Forbidden = 1e9 (numerical "infinity" stable for graph algorithms).

inf: 1.0e+9  # implementation of "forbidden" / mathematical infinity

tiers:
  safe_for_kid:
    lts_allowed: [1]
    main:     [1.0, 1.0e+9, 1.0e+9]
    fallback: [1.0, 5.0,   20.0]

  safe_for_parent:
    lts_allowed: [1, 2]
    main:     [1.0, 1.2, 1.0e+9]
    fallback: [1.0, 1.2, 10.0]

  not_safe:
    lts_allowed: [1, 2, 3]
    main:     [1.0, 1.2, 1.5]
    fallback: [1.0, 1.2, 1.5]
```

- [ ] **Step 2: Commit**

```bash
git add chicago-bike-advocacy-map/prep/config/routing_weights.yaml
git commit -m "feat(bikemap): add canonical routing weights config (spec §0.1)"
```

---

## Task 5: Vendor Socrata Client from chicago-pipeline

The user has a battle-tested Socrata client at `chicago-pipeline/pipeline/socrata.py`. Vendor it here rather than re-implementing. (Yes, this is duplication; extracting to a shared package is a future cleanup.)

**Files:**
- Create: `chicago-bike-advocacy-map/prep/socrata.py` (vendored copy)

- [ ] **Step 1: Copy the Socrata client**

```bash
cp chicago-pipeline/pipeline/socrata.py chicago-bike-advocacy-map/prep/socrata.py
```

- [ ] **Step 2: Verify it imports cleanly**

```bash
cd chicago-bike-advocacy-map
.venv/bin/python -c "from prep.socrata import SocrataClient; print('ok')"
```

Expected: `ok`. If import errors, the source file likely depends on something in `chicago-pipeline.pipeline` — fix imports inline (only stdlib + `requests` should be needed).

- [ ] **Step 3: Add a vendor note at the top of the file**

Add as the first line of `chicago-bike-advocacy-map/prep/socrata.py`:

```python
# Vendored from chicago-pipeline/pipeline/socrata.py @ 2026-05-05.
# When upstream changes meaningfully, copy the new version. This duplication
# is intentional v1; extraction to a shared package is future work.
```

- [ ] **Step 4: Commit**

```bash
git add chicago-bike-advocacy-map/prep/socrata.py
git commit -m "feat(bikemap): vendor Socrata client from chicago-pipeline"
```

---

## Task 6: Fetcher Base Class (TDD)

**Files:**
- Create: `chicago-bike-advocacy-map/prep/fetchers/base.py`
- Test: `chicago-bike-advocacy-map/tests/prep/test_fetchers_base.py`

The base class defines a uniform interface (each fetcher returns a path to a cached file) plus a versioned cache directory pattern (latest 3 snapshots retained on disk per spec §3.9).

- [ ] **Step 1: Write the failing test**

```python
# tests/prep/test_fetchers_base.py
import datetime as dt
from pathlib import Path

import pytest

from prep.fetchers.base import (
    Fetcher,
    FetchResult,
    rotate_snapshots,
    today_snapshot_dir,
)


def test_today_snapshot_dir_returns_dated_subdir(tmp_path: Path) -> None:
    out = today_snapshot_dir(tmp_path, today=dt.date(2026, 5, 5))
    assert out == tmp_path / "2026-05-05"
    assert out.exists()


def test_rotate_snapshots_keeps_only_n_most_recent(tmp_path: Path) -> None:
    # create 5 dated subdirs
    for d in ("2026-05-01", "2026-05-02", "2026-05-03", "2026-05-04", "2026-05-05"):
        (tmp_path / d).mkdir()
        (tmp_path / d / "marker.txt").write_text(d)

    rotate_snapshots(tmp_path, keep=3)

    remaining = sorted(p.name for p in tmp_path.iterdir() if p.is_dir())
    assert remaining == ["2026-05-03", "2026-05-04", "2026-05-05"]


def test_fetcher_subclass_must_implement_fetch(tmp_path: Path) -> None:
    class Incomplete(Fetcher):
        name = "incomplete"

    f = Incomplete()
    with pytest.raises(NotImplementedError):
        f.fetch(tmp_path)


def test_fetcher_concrete_subclass_runs(tmp_path: Path) -> None:
    class FakeFetcher(Fetcher):
        name = "fake"

        def fetch(self, cache_dir: Path) -> FetchResult:
            target = cache_dir / "out.txt"
            target.write_text("data")
            return FetchResult(path=target, record_count=1, status="OK", warnings=[])

    f = FakeFetcher()
    result = f.fetch(tmp_path)
    assert result.path.read_text() == "data"
    assert result.record_count == 1
    assert result.status == "OK"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/prep/test_fetchers_base.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'prep.fetchers.base'`

- [ ] **Step 3: Implement the base class**

```python
# prep/fetchers/base.py
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class FetchResult:
    """Outcome of a single fetch."""
    path: Path
    record_count: int
    status: str  # "OK" | "WARN" | "FAIL"
    warnings: list[str] = field(default_factory=list)


class Fetcher:
    """Base class for source fetchers. Subclasses set `name` and implement `fetch`."""
    name: str = ""

    def fetch(self, cache_dir: Path) -> FetchResult:
        """Fetch the source data and return a FetchResult.

        cache_dir: an existing directory where the fetcher should write its output.
        """
        raise NotImplementedError(f"{type(self).__name__}.fetch not implemented")


def today_snapshot_dir(parent: Path, today: dt.date | None = None) -> Path:
    """Return parent/<YYYY-MM-DD>/, creating it if needed."""
    today = today or dt.date.today()
    out = parent / today.isoformat()
    out.mkdir(parents=True, exist_ok=True)
    return out


def rotate_snapshots(parent: Path, keep: int = 3) -> None:
    """Delete dated subdirectories under parent, keeping only the `keep` most recent.

    Subdirectories are recognized by ISO date format (YYYY-MM-DD).
    """
    import shutil

    if not parent.exists():
        return

    dated: list[tuple[dt.date, Path]] = []
    for child in parent.iterdir():
        if not child.is_dir():
            continue
        try:
            d = dt.date.fromisoformat(child.name)
            dated.append((d, child))
        except ValueError:
            continue

    dated.sort(reverse=True)
    for _, path in dated[keep:]:
        shutil.rmtree(path)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/prep/test_fetchers_base.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add chicago-bike-advocacy-map/prep/fetchers/base.py chicago-bike-advocacy-map/tests/prep/test_fetchers_base.py
git commit -m "feat(bikemap): add fetcher base class and snapshot rotation"
```

---

## Task 7: CMAP HIN Fetcher (TDD)

**Files:**
- Create: `chicago-bike-advocacy-map/prep/fetchers/hin.py`
- Test: `chicago-bike-advocacy-map/tests/prep/test_hin_fetcher.py`
- Create: `chicago-bike-advocacy-map/tests/fixtures/hin_segments_response.json`
- Create: `chicago-bike-advocacy-map/tests/fixtures/hin_intersections_response.json`

The HIN fetcher pulls segments and intersections from the Cook County HIN ArcGIS Feature Service via paginated `/query` calls.

- [ ] **Step 1: Create fixture files**

`tests/fixtures/hin_segments_response.json`:

```json
{
  "objectIdFieldName": "OBJECTID",
  "globalIdFieldName": "GlobalID",
  "geometryType": "esriGeometryPolyline",
  "spatialReference": {"wkid": 4326},
  "features": [
    {
      "attributes": {
        "OBJECTID": 1,
        "STNAME": "WESTERN AVE",
        "FROM_": "FOSTER AVE",
        "TO_": "LAWRENCE AVE",
        "MODE_BIKE": 1,
        "MODE_PED": 1,
        "SEVERITY_RANK": 4
      },
      "geometry": {
        "paths": [[[-87.689, 41.975], [-87.689, 41.968]]]
      }
    },
    {
      "attributes": {
        "OBJECTID": 2,
        "STNAME": "FOSTER AVE",
        "FROM_": "WESTERN AVE",
        "TO_": "DAMEN AVE",
        "MODE_BIKE": 0,
        "MODE_PED": 1,
        "SEVERITY_RANK": 3
      },
      "geometry": {
        "paths": [[[-87.689, 41.975], [-87.679, 41.975]]]
      }
    }
  ]
}
```

`tests/fixtures/hin_intersections_response.json`:

```json
{
  "objectIdFieldName": "OBJECTID",
  "globalIdFieldName": "GlobalID",
  "geometryType": "esriGeometryPoint",
  "spatialReference": {"wkid": 4326},
  "features": [
    {
      "attributes": {
        "OBJECTID": 1,
        "INTERSECTION_NAME": "Western Ave & Foster Ave",
        "MODE_BIKE": 1,
        "MODE_PED": 1,
        "SEVERITY_RANK": 5
      },
      "geometry": {"x": -87.689, "y": 41.975}
    }
  ]
}
```

- [ ] **Step 2: Write the failing test**

```python
# tests/prep/test_hin_fetcher.py
import json
from pathlib import Path

import pytest
import responses

from prep.fetchers.hin import HinFetcher


@pytest.fixture
def segments_url() -> str:
    return "https://example.com/services/HIN_Segments/FeatureServer/0"


@pytest.fixture
def intersections_url() -> str:
    return "https://example.com/services/HIN_Intersections/FeatureServer/0"


@responses.activate
def test_hin_fetcher_writes_two_geojson_files(
    cache_dir: Path,
    fixtures_dir: Path,
    segments_url: str,
    intersections_url: str,
) -> None:
    # Mock segment query
    seg_payload = json.loads((fixtures_dir / "hin_segments_response.json").read_text())
    responses.add(
        responses.GET,
        f"{segments_url}/query",
        json=seg_payload,
        status=200,
    )
    # Mock intersection query
    int_payload = json.loads((fixtures_dir / "hin_intersections_response.json").read_text())
    responses.add(
        responses.GET,
        f"{intersections_url}/query",
        json=int_payload,
        status=200,
    )

    fetcher = HinFetcher(
        segments_url=segments_url,
        intersections_url=intersections_url,
    )
    result = fetcher.fetch(cache_dir)

    assert result.status == "OK"
    assert result.record_count == 3  # 2 segments + 1 intersection
    seg_path = cache_dir / "hin_segments.geojson"
    int_path = cache_dir / "hin_intersections.geojson"
    assert seg_path.exists()
    assert int_path.exists()

    seg_geo = json.loads(seg_path.read_text())
    assert seg_geo["type"] == "FeatureCollection"
    assert len(seg_geo["features"]) == 2
    assert seg_geo["features"][0]["properties"]["STNAME"] == "WESTERN AVE"
    assert seg_geo["features"][0]["geometry"]["type"] == "LineString"

    int_geo = json.loads(int_path.read_text())
    assert int_geo["type"] == "FeatureCollection"
    assert int_geo["features"][0]["geometry"]["type"] == "Point"


@responses.activate
def test_hin_fetcher_handles_http_error(
    cache_dir: Path,
    segments_url: str,
    intersections_url: str,
) -> None:
    responses.add(responses.GET, f"{segments_url}/query", status=503)
    responses.add(responses.GET, f"{intersections_url}/query", status=200, json={"features": []})

    fetcher = HinFetcher(
        segments_url=segments_url,
        intersections_url=intersections_url,
    )
    result = fetcher.fetch(cache_dir)

    assert result.status == "FAIL"
    assert any("503" in w for w in result.warnings)


@responses.activate
def test_hin_fetcher_paginates_until_transfer_limit_clears(
    cache_dir: Path,
    segments_url: str,
    intersections_url: str,
) -> None:
    # First page: 2 features, exceededTransferLimit=true → fetcher must request second page.
    responses.add(
        responses.GET,
        f"{segments_url}/query",
        json={
            "spatialReference": {"wkid": 4326},
            "exceededTransferLimit": True,
            "features": [
                {"attributes": {"OBJECTID": 1, "STNAME": "A", "MODE_BIKE": 1, "MODE_PED": 0, "SEVERITY_RANK": 3},
                 "geometry": {"paths": [[[-87.7, 41.9], [-87.6, 41.9]]]}},
                {"attributes": {"OBJECTID": 2, "STNAME": "B", "MODE_BIKE": 1, "MODE_PED": 1, "SEVERITY_RANK": 4},
                 "geometry": {"paths": [[[-87.7, 41.91], [-87.6, 41.91]]]}},
            ],
        },
        status=200,
    )
    # Second page: 1 feature, no exceededTransferLimit → fetcher stops.
    responses.add(
        responses.GET,
        f"{segments_url}/query",
        json={
            "spatialReference": {"wkid": 4326},
            "features": [
                {"attributes": {"OBJECTID": 3, "STNAME": "C", "MODE_BIKE": 0, "MODE_PED": 1, "SEVERITY_RANK": 2},
                 "geometry": {"paths": [[[-87.7, 41.92], [-87.6, 41.92]]]}},
            ],
        },
        status=200,
    )
    # Intersections: just one page.
    responses.add(
        responses.GET,
        f"{intersections_url}/query",
        json={"spatialReference": {"wkid": 4326}, "features": []},
        status=200,
    )

    fetcher = HinFetcher(
        segments_url=segments_url, intersections_url=intersections_url,
    )
    result = fetcher.fetch(cache_dir)
    assert result.status == "OK"

    seg_geo = json.loads((cache_dir / "hin_segments.geojson").read_text())
    # All 3 features (2 from page 1 + 1 from page 2) should be merged.
    assert len(seg_geo["features"]) == 3


@responses.activate
def test_hin_fetcher_raises_on_unexpected_spatial_reference(
    cache_dir: Path,
    segments_url: str,
    intersections_url: str,
) -> None:
    # Server responds with EPSG:3435 (state plane) instead of 4326 — fetcher must NOT silently accept.
    responses.add(
        responses.GET,
        f"{segments_url}/query",
        json={
            "spatialReference": {"wkid": 3435},
            "features": [
                {"attributes": {"OBJECTID": 1, "STNAME": "A"},
                 "geometry": {"paths": [[[1.1e6, 1.9e6], [1.2e6, 1.9e6]]]}},
            ],
        },
        status=200,
    )
    responses.add(
        responses.GET,
        f"{intersections_url}/query",
        json={"spatialReference": {"wkid": 4326}, "features": []},
        status=200,
    )

    fetcher = HinFetcher(
        segments_url=segments_url, intersections_url=intersections_url,
    )
    result = fetcher.fetch(cache_dir)
    assert result.status == "FAIL"
    assert any("spatial reference" in w.lower() or "3435" in w for w in result.warnings)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/prep/test_hin_fetcher.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'prep.fetchers.hin'`

- [ ] **Step 4: Implement the HIN fetcher**

```python
# prep/fetchers/hin.py
from __future__ import annotations

import json
from pathlib import Path

import requests

from prep.fetchers.base import FetchResult, Fetcher


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

    def _query_to_geojson(self, base_url: str) -> dict:
        """Page through the feature service until all features are fetched.

        ArcGIS Feature Services typically cap at 1000-2000 features per
        /query call. We loop with `resultOffset` and `resultRecordCount`
        until the server stops returning new features. This is the canonical
        ArcGIS pagination idiom.

        Also: we explicitly request `outSR=4326` but verify in the response
        that the returned spatial reference is 4326 — if not, we raise
        rather than silently treat coords as the wrong CRS.
        """
        page_size = 1000
        offset = 0
        all_features: list[dict] = []

        while True:
            params = {
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

            # Verify spatial reference of the response.
            sr = (data.get("spatialReference") or {}).get("wkid")
            if sr is not None and sr not in (4326, 4269):
                # 4269 = NAD83 (close enough to 4326 for most consumers)
                raise RuntimeError(
                    f"unexpected spatial reference {sr} from {base_url} "
                    f"(expected 4326). Server may not honor outSR — reproject before consuming."
                )

            page_features = data.get("features", [])
            if not page_features:
                break
            all_features.extend(page_features)

            # The server signals "more pages exist" via exceededTransferLimit.
            if not data.get("exceededTransferLimit"):
                break
            offset += page_size

        return _esri_to_geojson({"features": all_features})


def _esri_to_geojson(esri: dict) -> dict:
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


def _esri_geom_to_geojson(g: dict) -> dict | None:
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
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/prep/test_hin_fetcher.py -v`
Expected: PASS (4 tests — basic, error, pagination, SR-mismatch)

- [ ] **Step 6: Commit**

```bash
git add chicago-bike-advocacy-map/prep/fetchers/hin.py chicago-bike-advocacy-map/tests/prep/test_hin_fetcher.py chicago-bike-advocacy-map/tests/fixtures/hin_segments_response.json chicago-bike-advocacy-map/tests/fixtures/hin_intersections_response.json
git commit -m "feat(bikemap): add CMAP HIN fetcher with ArcGIS-to-GeoJSON conversion"
```

---

## Task 7a: Socrata Geometry/Location Parsing Helper (TDD)

**Files:**
- Create: `chicago-bike-advocacy-map/prep/fetchers/socrata_geom.py`
- Test: `chicago-bike-advocacy-map/tests/prep/test_socrata_geom.py`

Chicago Data Portal returns geometry in *multiple formats* across datasets. The same `the_geom` field might be:

- A GeoJSON-style dict: `{"type":"MultiLineString","coordinates":[[[...]]]}`
- A WKT string: `"MULTILINESTRING ((-87.6 41.9, ...))"`
- A `_human_address` JSON-string: `'{"address":"...","city":"...","state":"...","zip":"..."}'`
- Separate `latitude` + `longitude` columns (no `the_geom` at all)

For point-typed POI fields like `location`, the variants include:
- A GeoJSON Point dict
- A SODA "human" array `[lat, lng]` (note: lat first, opposite GeoJSON convention)
- A string `"(41.945, -87.683)"`

Tasks 8/9/10 use these formats unpredictably. A shared helper normalizes any of them to GeoJSON. **Without this, real CDP fetches will silently drop rows.**

- [ ] **Step 1: Write the failing test**

```python
# tests/prep/test_socrata_geom.py
import pytest

from prep.fetchers.socrata_geom import (
    extract_geometry,
    extract_point_location,
)


def test_extract_geometry_geojson_dict_passthrough() -> None:
    row = {"the_geom": {"type": "MultiLineString", "coordinates": [[[-87.6, 41.9], [-87.5, 41.9]]]}}
    geom = extract_geometry(row)
    assert geom["type"] == "MultiLineString"
    assert geom["coordinates"][0][0] == [-87.6, 41.9]


def test_extract_geometry_wkt_string_converted() -> None:
    row = {"the_geom": "MULTILINESTRING ((-87.6 41.9, -87.5 41.9))"}
    geom = extract_geometry(row)
    assert geom["type"] == "MultiLineString"
    assert geom["coordinates"][0][0] == pytest.approx([-87.6, 41.9])


def test_extract_geometry_returns_none_when_missing() -> None:
    assert extract_geometry({"name": "X"}) is None


def test_extract_geometry_returns_none_for_human_address_object() -> None:
    """_human_address is metadata, not geometry; skip rather than parse."""
    row = {"the_geom": '{"address": "1234 W Foo St", "city": "Chicago"}'}
    assert extract_geometry(row) is None


def test_extract_geometry_json_encoded_geojson_string_parsed() -> None:
    """Some CDP datasets emit GeoJSON serialized as a string."""
    row = {"the_geom": '{"type":"Point","coordinates":[-87.683,41.945]}'}
    geom = extract_geometry(row)
    assert geom == {"type": "Point", "coordinates": [-87.683, 41.945]}


def test_extract_point_location_geojson_dict() -> None:
    row = {"location": {"type": "Point", "coordinates": [-87.683, 41.945]}}
    pt = extract_point_location(row)
    assert pt == {"type": "Point", "coordinates": [-87.683, 41.945]}


def test_extract_point_location_array_lat_lng() -> None:
    """Socrata 'human' format puts lat first, lng second — opposite of GeoJSON."""
    row = {"location": [41.945, -87.683]}
    pt = extract_point_location(row)
    # We must flip to GeoJSON's [lng, lat] order.
    assert pt == {"type": "Point", "coordinates": [-87.683, 41.945]}


def test_extract_point_location_paren_string() -> None:
    row = {"location": "(41.945, -87.683)"}
    pt = extract_point_location(row)
    assert pt == {"type": "Point", "coordinates": [-87.683, 41.945]}


def test_extract_point_location_separate_lat_lng_columns() -> None:
    row = {"latitude": "41.945", "longitude": "-87.683"}
    pt = extract_point_location(row)
    assert pt == {"type": "Point", "coordinates": [-87.683, 41.945]}


def test_extract_point_location_returns_none_when_missing() -> None:
    assert extract_point_location({"name": "X"}) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/prep/test_socrata_geom.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement the helpers**

```python
# prep/fetchers/socrata_geom.py
"""Normalize the wildly inconsistent geometry/location formats Chicago Data
Portal returns across datasets to GeoJSON dicts.

Drop-in replacement for `row.pop("the_geom")` / `row.pop("location")` patterns
in fetchers that need to handle real-world CDP payloads (test fixtures often
only cover one format; production data hits all of them).
"""
from __future__ import annotations

import json
import re
from typing import Any

from shapely import wkt as _wkt
from shapely.geometry import mapping


_PAREN_POINT_RE = re.compile(r"^\(\s*([+-]?\d+\.?\d*)\s*,\s*([+-]?\d+\.?\d*)\s*\)$")

_GEOJSON_TYPES = frozenset({
    "Point", "LineString", "Polygon",
    "MultiPoint", "MultiLineString", "MultiPolygon",
    "GeometryCollection",
})


def extract_geometry(row: dict[str, Any]) -> dict | None:
    """Extract a GeoJSON-shaped geometry from `the_geom` or fall back to lat/lng.

    Returns None if no usable geometry found. The row is NOT mutated.

    Recognized formats:
      - GeoJSON dict: passthrough
      - GeoJSON serialized as JSON string: parsed and returned
      - WKT string (`POINT(...)`, `MULTILINESTRING(...)` etc.): parsed via shapely
      - `_human_address`-style JSON string envelope: NOT geometry → None
      - Separate `latitude` + `longitude` columns: a Point GeoJSON
    """
    raw = row.get("the_geom")

    if isinstance(raw, dict):
        # Already GeoJSON.
        if raw.get("type") in _GEOJSON_TYPES:
            return raw
        # Probably a _human_address-style envelope; not geometry.
        return None

    if isinstance(raw, str):
        s = raw.strip()
        if s.startswith("{"):
            # JSON-encoded — could be GeoJSON or _human_address envelope.
            try:
                parsed = json.loads(s)
            except Exception:  # noqa: BLE001
                parsed = None
            if isinstance(parsed, dict) and parsed.get("type") in _GEOJSON_TYPES:
                return parsed
            # Either malformed JSON or a non-geometry envelope.
            return None
        # Otherwise try WKT.
        try:
            geom = _wkt.loads(s)
            return mapping(geom)
        except Exception:  # noqa: BLE001
            pass

    # Fall back to separate lat/lng columns.
    return extract_point_location(row)


def extract_point_location(row: dict[str, Any]) -> dict | None:
    """Extract a GeoJSON Point from `location` or `latitude`/`longitude` fields.

    Recognized formats:
      - GeoJSON dict: passthrough
      - SODA "human" array [lat, lng]: flipped to GeoJSON [lng, lat]
      - "(lat, lng)" paren string: parsed and flipped
      - separate latitude+longitude columns: combined into Point
    """
    raw = row.get("location")

    if isinstance(raw, dict) and raw.get("type") == "Point":
        return raw

    if isinstance(raw, (list, tuple)) and len(raw) == 2:
        lat, lng = raw
        return {"type": "Point", "coordinates": [float(lng), float(lat)]}

    if isinstance(raw, str):
        m = _PAREN_POINT_RE.match(raw.strip())
        if m:
            lat, lng = m.group(1), m.group(2)
            return {"type": "Point", "coordinates": [float(lng), float(lat)]}
        # Maybe a JSON-encoded GeoJSON Point.
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict) and parsed.get("type") == "Point":
                return parsed
        except Exception:  # noqa: BLE001
            pass

    # Fall back to separate columns.
    lat = row.get("latitude")
    lng = row.get("longitude")
    if lat is not None and lng is not None:
        try:
            return {"type": "Point", "coordinates": [float(lng), float(lat)]}
        except (TypeError, ValueError):
            return None

    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/prep/test_socrata_geom.py -v`
Expected: PASS (10 tests — added GeoJSON-as-string variant)

- [ ] **Step 5: Commit**

```bash
git add chicago-bike-advocacy-map/prep/fetchers/socrata_geom.py chicago-bike-advocacy-map/tests/prep/test_socrata_geom.py
git commit -m "feat(bikemap): add Socrata geometry/location format-normalization helper"
```

---

## Task 8: CDOT Bike Facilities Fetcher (TDD)

**Files:**
- Create: `chicago-bike-advocacy-map/prep/fetchers/cdot_sanity.py`
- Test: `chicago-bike-advocacy-map/tests/prep/test_cdot_sanity_fetcher.py`
- Create: `chicago-bike-advocacy-map/tests/fixtures/cdot_bikeways_response.json`

CDOT data is consumed as a sanity check only (per spec §3.3) — discrepancies vs. OSM are flagged in `prep_report.md` but OSM wins.

This fetcher uses `prep/fetchers/socrata_geom.py` (Task 7a) to handle the *several* formats Socrata uses for `the_geom` across datasets — without it, real CDP fetches silently drop rows whose format doesn't match a hard-coded assumption.

- [ ] **Step 1: Create fixture file**

`tests/fixtures/cdot_bikeways_response.json`:

```json
[
  {
    "objectid": "1",
    "facility_t": "PROTECTED BIKE LANE",
    "street": "MILWAUKEE AVE",
    "f_st": "DIVISION ST",
    "t_st": "ASHLAND AVE",
    "the_geom": {
      "type": "MultiLineString",
      "coordinates": [[[-87.665, 41.903], [-87.667, 41.910]]]
    }
  },
  {
    "objectid": "2",
    "facility_t": "BIKE LANE",
    "street": "ELSTON AVE",
    "f_st": "FULLERTON AVE",
    "t_st": "DIVERSEY AVE",
    "the_geom": {
      "type": "MultiLineString",
      "coordinates": [[[-87.690, 41.925], [-87.695, 41.932]]]
    }
  }
]
```

- [ ] **Step 2: Write the failing test**

```python
# tests/prep/test_cdot_sanity_fetcher.py
import json
from pathlib import Path

import responses

from prep.fetchers.cdot_sanity import CdotBikewaysFetcher


@responses.activate
def test_cdot_sanity_fetcher_writes_geojson(
    cache_dir: Path,
    fixtures_dir: Path,
) -> None:
    payload = json.loads((fixtures_dir / "cdot_bikeways_response.json").read_text())
    responses.add(
        responses.GET,
        "https://data.cityofchicago.org/resource/3w5d-sru8.json",
        json=payload,
        status=200,
    )

    fetcher = CdotBikewaysFetcher(domain="data.cityofchicago.org", dataset_id="3w5d-sru8")
    result = fetcher.fetch(cache_dir)

    assert result.status == "OK"
    assert result.record_count == 2
    out = cache_dir / "cdot_bikeways.geojson"
    assert out.exists()

    geo = json.loads(out.read_text())
    assert geo["type"] == "FeatureCollection"
    assert len(geo["features"]) == 2
    assert geo["features"][0]["properties"]["facility_t"] == "PROTECTED BIKE LANE"
    assert geo["features"][0]["geometry"]["type"] == "MultiLineString"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/prep/test_cdot_sanity_fetcher.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 4: Implement the fetcher**

```python
# prep/fetchers/cdot_sanity.py
from __future__ import annotations

import json
from pathlib import Path

from prep.fetchers.base import FetchResult, Fetcher
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
            # Strip the original geometry field from the properties.
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
```

- [ ] **Step 5: Add a fixture variant test for WKT-format `the_geom`**

Append to `tests/prep/test_cdot_sanity_fetcher.py`:

```python
@responses.activate
def test_cdot_sanity_fetcher_handles_wkt_geometry_format(cache_dir: Path) -> None:
    """Some CDP datasets return `the_geom` as WKT instead of GeoJSON."""
    payload = [
        {
            "objectid": "1",
            "facility_t": "PROTECTED BIKE LANE",
            "street": "MILWAUKEE AVE",
            "the_geom": "MULTILINESTRING ((-87.665 41.903, -87.667 41.910))",
        },
    ]
    responses.add(
        responses.GET,
        "https://data.cityofchicago.org/resource/3w5d-sru8.json",
        json=payload,
        status=200,
    )
    fetcher = CdotBikewaysFetcher(domain="data.cityofchicago.org", dataset_id="3w5d-sru8")
    result = fetcher.fetch(cache_dir)
    assert result.status == "OK"
    assert result.record_count == 1
    geo = json.loads((cache_dir / "cdot_bikeways.geojson").read_text())
    assert geo["features"][0]["geometry"]["type"] == "MultiLineString"
```

- [ ] **Step 6: Run test to verify both pass**

Run: `pytest tests/prep/test_cdot_sanity_fetcher.py -v`
Expected: PASS (2 tests — GeoJSON-format and WKT-format)

- [ ] **Step 7: Commit**

```bash
git add chicago-bike-advocacy-map/prep/fetchers/cdot_sanity.py chicago-bike-advocacy-map/tests/prep/test_cdot_sanity_fetcher.py chicago-bike-advocacy-map/tests/fixtures/cdot_bikeways_response.json
git commit -m "feat(bikemap): add CDOT bike facilities fetcher (sanity check, multi-format)"
```

---

## Task 9: Chicago Speed Limits Fetcher (TDD)

**Files:**
- Create: `chicago-bike-advocacy-map/prep/fetchers/speed_limits.py`
- Test: `chicago-bike-advocacy-map/tests/prep/test_speed_limits_fetcher.py`
- Create: `chicago-bike-advocacy-map/tests/fixtures/speed_limits_response.json`

Same pattern as CDOT bikeways: Socrata-backed, MultiLineString geometries, written as GeoJSON.

- [ ] **Step 1: Create fixture file**

`tests/fixtures/speed_limits_response.json`:

```json
[
  {
    "objectid": "1",
    "street_nam": "WESTERN AVE",
    "speed_limit": "30",
    "the_geom": {
      "type": "MultiLineString",
      "coordinates": [[[-87.689, 41.975], [-87.689, 41.985]]]
    }
  },
  {
    "objectid": "2",
    "street_nam": "FOSTER AVE",
    "speed_limit": "30",
    "the_geom": {
      "type": "MultiLineString",
      "coordinates": [[[-87.689, 41.975], [-87.679, 41.975]]]
    }
  }
]
```

- [ ] **Step 2: Write the failing test**

```python
# tests/prep/test_speed_limits_fetcher.py
import json
from pathlib import Path

import responses

from prep.fetchers.speed_limits import SpeedLimitsFetcher


@responses.activate
def test_speed_limits_fetcher_writes_geojson(
    cache_dir: Path,
    fixtures_dir: Path,
) -> None:
    payload = json.loads((fixtures_dir / "speed_limits_response.json").read_text())
    responses.add(
        responses.GET,
        "https://data.cityofchicago.org/resource/spqx-js37.json",
        json=payload,
        status=200,
    )

    fetcher = SpeedLimitsFetcher(domain="data.cityofchicago.org", dataset_id="spqx-js37")
    result = fetcher.fetch(cache_dir)

    assert result.status == "OK"
    assert result.record_count == 2
    out = cache_dir / "chicago_speed_limits.geojson"
    geo = json.loads(out.read_text())
    assert geo["features"][0]["properties"]["speed_limit"] == "30"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/prep/test_speed_limits_fetcher.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 4: Implement the fetcher**

```python
# prep/fetchers/speed_limits.py
from __future__ import annotations

import json
from pathlib import Path

from prep.fetchers.base import FetchResult, Fetcher
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
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/prep/test_speed_limits_fetcher.py -v`
Expected: PASS (1 test)

- [ ] **Step 6: Commit**

```bash
git add chicago-bike-advocacy-map/prep/fetchers/speed_limits.py chicago-bike-advocacy-map/tests/prep/test_speed_limits_fetcher.py chicago-bike-advocacy-map/tests/fixtures/speed_limits_response.json
git commit -m "feat(bikemap): add Chicago speed limits fetcher"
```

---

## Task 10: CDP POIs Fetcher — Alderman + Library (TDD)

**Files:**
- Create: `chicago-bike-advocacy-map/prep/fetchers/pois_cdp.py`
- Test: `chicago-bike-advocacy-map/tests/prep/test_pois_cdp_fetcher.py`
- Create: `chicago-bike-advocacy-map/tests/fixtures/cdp_alderman_offices.json`
- Create: `chicago-bike-advocacy-map/tests/fixtures/cdp_libraries.json`

Per spec §3.3, CDP fetchers are reserved for categories brokenspoke doesn't emit: alderman/ward offices and CPL library branches. Other POI categories come from brokenspoke's exports (Task 13).

- [ ] **Step 1: Create fixture files**

`tests/fixtures/cdp_alderman_offices.json`:

```json
[
  {
    "ward": "1",
    "alderman": "Daniel La Spata",
    "address": "2740 W NORTH AVE",
    "city": "Chicago",
    "state": "IL",
    "zip": "60647",
    "phone": "(773) 278-0101",
    "location": {
      "type": "Point",
      "coordinates": [-87.694, 41.910]
    }
  },
  {
    "ward": "47",
    "alderman": "Matt Martin",
    "address": "4243 N LINCOLN AVE",
    "city": "Chicago",
    "state": "IL",
    "zip": "60618",
    "phone": "(773) 868-4747",
    "location": {
      "type": "Point",
      "coordinates": [-87.683, 41.959]
    }
  }
]
```

`tests/fixtures/cdp_libraries.json`:

```json
[
  {
    "name_": "Lincoln Park",
    "hours": "Mon-Thu 10-8, Fri-Sat 9-5, Sun 1-5",
    "address": "1150 W FULLERTON AVE",
    "city": "Chicago",
    "state": "IL",
    "zip": "60614",
    "location": {
      "type": "Point",
      "coordinates": [-87.659, 41.925]
    }
  }
]
```

- [ ] **Step 2: Write the failing test**

```python
# tests/prep/test_pois_cdp_fetcher.py
import json
from pathlib import Path

import responses

from prep.fetchers.pois_cdp import CdpPoisFetcher


@responses.activate
def test_cdp_pois_fetcher_writes_two_geojson_files(
    cache_dir: Path,
    fixtures_dir: Path,
) -> None:
    alderman = json.loads((fixtures_dir / "cdp_alderman_offices.json").read_text())
    libraries = json.loads((fixtures_dir / "cdp_libraries.json").read_text())
    responses.add(
        responses.GET,
        "https://data.cityofchicago.org/resource/htai-wnw4.json",
        json=alderman,
        status=200,
    )
    responses.add(
        responses.GET,
        "https://data.cityofchicago.org/resource/x8fc-8rcq.json",
        json=libraries,
        status=200,
    )

    fetcher = CdpPoisFetcher(
        domain="data.cityofchicago.org",
        alderman_dataset_id="htai-wnw4",
        library_dataset_id="x8fc-8rcq",
    )
    result = fetcher.fetch(cache_dir)

    assert result.status == "OK"
    assert result.record_count == 3  # 2 alderman + 1 library

    aldr_path = cache_dir / "cdp_alderman_offices.geojson"
    lib_path = cache_dir / "cdp_libraries.geojson"
    assert aldr_path.exists()
    assert lib_path.exists()

    aldr_geo = json.loads(aldr_path.read_text())
    assert aldr_geo["features"][0]["properties"]["ward"] == "1"
    assert aldr_geo["features"][0]["geometry"]["coordinates"] == [-87.694, 41.910]

    lib_geo = json.loads(lib_path.read_text())
    assert lib_geo["features"][0]["properties"]["name_"] == "Lincoln Park"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/prep/test_pois_cdp_fetcher.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 4: Implement the fetcher**

```python
# prep/fetchers/pois_cdp.py
from __future__ import annotations

import json
from pathlib import Path

from prep.fetchers.base import FetchResult, Fetcher
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
            # Strip raw location/lat/lng to keep the properties clean.
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
```

- [ ] **Step 5: Add a test variant for separate latitude/longitude columns**

Some CDP POI datasets emit `latitude` and `longitude` as separate string columns instead of a `location` field. Append to `tests/prep/test_pois_cdp_fetcher.py`:

```python
@responses.activate
def test_cdp_pois_fetcher_handles_separate_lat_lng_columns(cache_dir: Path) -> None:
    """Some CDP datasets emit latitude+longitude as separate string columns."""
    alderman = [
        {
            "ward": "5",
            "alderman": "Test Alder",
            "address": "1 N State St",
            "latitude": "41.883",
            "longitude": "-87.628",
        },
    ]
    libraries = [
        {
            "name_": "Test Library",
            "address": "1 W Foo Pl",
            "latitude": "41.900",
            "longitude": "-87.650",
        },
    ]
    responses.add(
        responses.GET,
        "https://data.cityofchicago.org/resource/htai-wnw4.json",
        json=alderman, status=200,
    )
    responses.add(
        responses.GET,
        "https://data.cityofchicago.org/resource/x8fc-8rcq.json",
        json=libraries, status=200,
    )
    fetcher = CdpPoisFetcher(
        domain="data.cityofchicago.org",
        alderman_dataset_id="htai-wnw4",
        library_dataset_id="x8fc-8rcq",
    )
    result = fetcher.fetch(cache_dir)
    assert result.status == "OK"
    assert result.record_count == 2
    aldr_geo = json.loads((cache_dir / "cdp_alderman_offices.geojson").read_text())
    assert aldr_geo["features"][0]["geometry"]["coordinates"] == [-87.628, 41.883]
```

- [ ] **Step 6: Run test to verify it passes**

Run: `pytest tests/prep/test_pois_cdp_fetcher.py -v`
Expected: PASS (2 tests — location-dict format and separate-columns format)

- [ ] **Step 7: Commit**

```bash
git add chicago-bike-advocacy-map/prep/fetchers/pois_cdp.py chicago-bike-advocacy-map/tests/prep/test_pois_cdp_fetcher.py chicago-bike-advocacy-map/tests/fixtures/cdp_alderman_offices.json chicago-bike-advocacy-map/tests/fixtures/cdp_libraries.json
git commit -m "feat(bikemap): add CDP fetcher for alderman + library POIs"
```

---

## Task 10a: Postgres Compose File for Brokenspoke

**Files:**
- Create: `chicago-bike-advocacy-map/docker/compose.brokenspoke.yml`

`brokenspoke-analyzer` requires a PostgreSQL+PostGIS instance reachable on a known Docker network (the brokenspoke README assumes the user runs `docker compose up -d` from brokenspoke's source tree, which provides a `compose.yml`). Since we're invoking brokenspoke as a remote Docker image *without* cloning its source, we must provide our own compose file. This file defines just the database and a stable network name our runner can bind to.

The `compose_file` path and `network_name` are already wired through `sources.yaml` and `BrokenspokeConfig` (Task 3) so this task only needs to write the actual compose file.

- [ ] **Step 1: Write the compose file**

```yaml
# docker/compose.brokenspoke.yml
# Postgres+PostGIS stack for brokenspoke-analyzer runs.
# Brought up by `prep.lts.runner.BrokenspokeRunner` before each LTS run, torn down after.
# The compose project name "bikemap-brokenspoke" (via the `name:` directive below)
# yields the default network "bikemap-brokenspoke_default" — which is what
# sources.yaml's `network_name` field references.

name: bikemap-brokenspoke

services:
  postgres:
    image: postgis/postgis:17-3.5
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
      POSTGRES_DB: postgres
    volumes:
      - bikemap_brokenspoke_pg:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD", "pg_isready", "-U", "postgres"]
      interval: 5s
      timeout: 5s
      retries: 10

volumes:
  bikemap_brokenspoke_pg:
```

- [ ] **Step 2: Smoke-check that the compose file is valid**

```bash
cd chicago-bike-advocacy-map
docker compose -f docker/compose.brokenspoke.yml -p bikemap-brokenspoke config
```

Expected: prints the resolved compose config without errors.

- [ ] **Step 3: Commit**

```bash
git add chicago-bike-advocacy-map/docker/compose.brokenspoke.yml
git commit -m "feat(bikemap): add postgres+postgis compose file for brokenspoke runs"
```

---

## Task 11: Brokenspoke Runner — Docker Subprocess Wrapper (TDD)

**Files:**
- Create: `chicago-bike-advocacy-map/prep/lts/runner.py`
- Test: `chicago-bike-advocacy-map/tests/prep/test_lts_runner.py`

The runner wraps the `docker compose up` + `docker run brokenspoke-analyzer` invocations from the brokenspoke README, against the postgres compose file from Task 10a. We test it by mocking `subprocess.run` — actual brokenspoke runs only happen during the smoke test (Task 26).

- [ ] **Step 1: Write the failing test**

```python
# tests/prep/test_lts_runner.py
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from prep.lts.runner import BrokenspokeRunner, BrokenspokeRunFailed


def make_runner(results_dir: Path, tmp_path: Path) -> BrokenspokeRunner:
    compose_file = tmp_path / "compose.brokenspoke.yml"
    compose_file.write_text("name: bikemap-brokenspoke\nservices: {}\n")
    return BrokenspokeRunner(
        image="ghcr.io/peopleforbikes/brokenspoke-analyzer:3.1.1",
        city_country="united states",
        city_name="chicago",
        city_state="illinois",
        city_fips="1714000",
        database_url="postgresql://postgres:postgres@postgres:5432/postgres",
        network_name="bikemap-brokenspoke_default",
        results_dir=results_dir,
        compose_file=compose_file,
    )


@patch("prep.lts.runner.subprocess.run")
def test_runner_invokes_docker_with_correct_args(
    mock_run: MagicMock, output_dir: Path, tmp_path: Path
) -> None:
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

    runner = make_runner(output_dir, tmp_path)
    runner.run()

    # Expect: compose up, configure, run, export, compose down — minimum 5 calls.
    assert mock_run.call_count >= 5
    # The 'run' call must include the city + FIPS args
    found_run = False
    for call in mock_run.call_args_list:
        args = call.args[0] if call.args else call.kwargs.get("args", [])
        if isinstance(args, list) and "run" in args and "1714000" in args:
            found_run = True
            assert "united states" in args
            assert "chicago" in args
            assert "illinois" in args
            break
    assert found_run, "expected 'run' invocation with chicago FIPS not found"


@patch("prep.lts.runner.subprocess.run")
def test_runner_passes_full_environment_not_only_database_url(
    mock_run: MagicMock, output_dir: Path, tmp_path: Path
) -> None:
    """env= must merge os.environ on EVERY subprocess call; otherwise PATH
    is empty and `docker` is not found. Regression-tests against the trap
    where someone refactors a per-call env dict and breaks PATH on later calls.
    """
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

    runner = make_runner(output_dir, tmp_path)
    runner.run()

    # Verify EVERY call (compose up, configure, run, export, compose down).
    assert mock_run.call_count >= 5, f"expected ≥5 subprocess calls, got {mock_run.call_count}"
    for i, call in enumerate(mock_run.call_args_list):
        passed_env = call.kwargs.get("env")
        assert passed_env is not None, f"call {i}: subprocess.run must receive env= explicitly"
        assert "PATH" in passed_env, f"call {i}: PATH must be propagated from os.environ"
        assert passed_env.get("DATABASE_URL", "").startswith("postgresql://"), \
            f"call {i}: DATABASE_URL must be set"


@patch("prep.lts.runner.subprocess.run")
def test_runner_raises_on_nonzero_exit(
    mock_run: MagicMock, output_dir: Path, tmp_path: Path
) -> None:
    mock_run.return_value = MagicMock(returncode=2, stdout="", stderr="boom")

    runner = make_runner(output_dir, tmp_path)
    with pytest.raises(BrokenspokeRunFailed) as exc:
        runner.run()
    assert "boom" in str(exc.value)


@patch("prep.lts.runner.subprocess.run")
def test_runner_returns_results_path(
    mock_run: MagicMock, output_dir: Path, tmp_path: Path
) -> None:
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    # Pretend the export step created the expected directory tree
    expected_dir = output_dir / "united-states" / "illinois" / "chicago"
    expected_dir.mkdir(parents=True)
    (expected_dir / "23.11").mkdir()

    runner = make_runner(output_dir, tmp_path)
    results_path = runner.run()
    # Should resolve to the deepest version subdir
    assert results_path.name == "23.11"
    assert results_path.parent == expected_dir
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/prep/test_lts_runner.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement the runner**

```python
# prep/lts/runner.py
from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


class BrokenspokeRunFailed(Exception):
    pass


@dataclass
class BrokenspokeRunner:
    image: str
    city_country: str
    city_name: str
    city_state: str
    city_fips: str
    database_url: str
    network_name: str
    results_dir: Path
    compose_file: Path  # Path to docker/compose.brokenspoke.yml (Task 10a)
    compose_project: str = "bikemap-brokenspoke"  # matches the `name:` in compose.brokenspoke.yml

    def run(self) -> Path:
        """Run the full brokenspoke pipeline. Returns path to the results directory.

        Steps (per brokenspoke README, adapted for our compose file):
          1. docker compose -f <our compose> -p <project> up -d (start postgres)
          2. configure database
          3. run analysis
          4. export local (bind-mount results into container)
          5. docker compose down (always, even on failure)
        """
        # CRITICAL: env must include the user's PATH or `docker` won't be found.
        # Build env by *extending* os.environ, not replacing it.
        env = {**os.environ, "DATABASE_URL": self.database_url}

        # 1. compose up our postgres
        self._run_cmd([
            "docker", "compose",
            "-f", str(self.compose_file),
            "-p", self.compose_project,
            "up", "-d", "--wait",
        ], env)

        try:
            # 2. configure brokenspoke against postgres
            self._run_cmd([
                "docker", "run", "--rm",
                "--network", self.network_name,
                "-e", "DATABASE_URL",
                self.image,
                "-vv", "configure", "custom", "4", "4096", "postgres",
            ], env)

            # 3. run analysis
            self._run_cmd([
                "docker", "run", "--rm",
                "--network", self.network_name,
                "-e", "DATABASE_URL",
                self.image,
                "-vv", "run", "--no-cache",
                self.city_country, self.city_name, self.city_state, self.city_fips,
            ], env)

            # 4. export local — bind-mount results dir into the container
            self.results_dir.mkdir(parents=True, exist_ok=True)
            uid_gid = f"{os.getuid()}:{os.getgid()}"
            self._run_cmd([
                "docker", "run", "--rm",
                "--network", self.network_name,
                "-u", uid_gid,
                "-v", f"{self.results_dir.resolve()}:/usr/src/app/results",
                "-e", "DATABASE_URL",
                self.image,
                "-vv", "export", "local",
                self.city_country, self.city_name, self.city_state,
            ], env)
        finally:
            # 5. compose down (always, even on failure). Log failures rather than
            # silently swallowing so we know if the volume is stuck.
            self._run_cmd([
                "docker", "compose",
                "-f", str(self.compose_file),
                "-p", self.compose_project,
                "down",
            ], env, check=False, log_failure=True)

        return self._resolve_results_path()

    def _run_cmd(
        self,
        cmd: list[str],
        env: dict[str, str],
        check: bool = True,
        log_failure: bool = False,
    ) -> None:
        result = subprocess.run(  # noqa: S603
            cmd,
            capture_output=True,
            text=True,
            env=env,
        )
        if result.returncode != 0:
            msg = (
                f"command failed (exit {result.returncode}): {' '.join(cmd)}\n"
                f"stdout: {result.stdout}\nstderr: {result.stderr}"
            )
            if check:
                raise BrokenspokeRunFailed(msg)
            if log_failure:
                logger.warning("brokenspoke teardown failure: %s", msg)

    def _resolve_results_path(self) -> Path:
        """Find the deepest version subdir under results_dir/<country>/<state>/<city>/."""
        base = (
            self.results_dir
            / self._slug(self.city_country)
            / self._slug(self.city_state)
            / self._slug(self.city_name)
        )
        if not base.exists():
            raise BrokenspokeRunFailed(f"expected results dir not found: {base}")
        version_dirs = [p for p in base.iterdir() if p.is_dir()]
        if not version_dirs:
            raise BrokenspokeRunFailed(f"no version subdirs under {base}")
        return sorted(version_dirs)[-1]

    @staticmethod
    def _slug(name: str) -> str:
        return name.lower().replace(" ", "-")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/prep/test_lts_runner.py -v`
Expected: PASS (4 tests — args, env-merging, raise-on-fail, results-path)

- [ ] **Step 5: Commit**

```bash
git add chicago-bike-advocacy-map/prep/lts/runner.py chicago-bike-advocacy-map/tests/prep/test_lts_runner.py
git commit -m "feat(bikemap): add brokenspoke-analyzer Docker runner wrapper"
```

---

## Task 12: Brokenspoke Ingest — Segments + Intersections (TDD)

**Files:**
- Create: `chicago-bike-advocacy-map/prep/lts/ingest.py`
- Test: `chicago-bike-advocacy-map/tests/prep/test_lts_ingest.py`
- Create: `chicago-bike-advocacy-map/tests/fixtures/neighborhood_ways_sample.geojson`
- Create: `chicago-bike-advocacy-map/tests/fixtures/neighborhood_ways_intersections_sample.geojson`

We don't yet know the *exact* property field names brokenspoke emits (research item §7.1 #2). For the ingest module we make these configurable via constants and use placeholder names (`lts`, `osm_id`, `ft_lts`/`tf_lts` for direction-aware, `lts_approach` for intersections). Task 26 verifies these against the real brokenspoke output and updates if needed.

- [ ] **Step 1: Create fixture: neighborhood_ways_sample.geojson**

```json
{
  "type": "FeatureCollection",
  "features": [
    {
      "type": "Feature",
      "properties": {
        "osm_id": 12345,
        "name": "W Foster Ave",
        "ft_lts": 4,
        "tf_lts": 4,
        "highway": "primary",
        "speed": 30
      },
      "geometry": {
        "type": "LineString",
        "coordinates": [[-87.689, 41.975], [-87.679, 41.975]]
      }
    },
    {
      "type": "Feature",
      "properties": {
        "osm_id": 67890,
        "name": "N Hoyne Ave",
        "ft_lts": 1,
        "tf_lts": 1,
        "highway": "residential",
        "speed": 25
      },
      "geometry": {
        "type": "LineString",
        "coordinates": [[-87.679, 41.975], [-87.679, 41.985]]
      }
    }
  ]
}
```

- [ ] **Step 2: Create fixture: neighborhood_ways_intersections_sample.geojson**

```json
{
  "type": "FeatureCollection",
  "features": [
    {
      "type": "Feature",
      "properties": {
        "osm_id": 999001,
        "lts_approach": 4,
        "signalized": true,
        "lanes_crossed": 6
      },
      "geometry": {
        "type": "Point",
        "coordinates": [-87.689, 41.975]
      }
    },
    {
      "type": "Feature",
      "properties": {
        "osm_id": 999002,
        "lts_approach": 2,
        "signalized": false,
        "lanes_crossed": 2
      },
      "geometry": {
        "type": "Point",
        "coordinates": [-87.679, 41.975]
      }
    }
  ]
}
```

- [ ] **Step 3: Write the failing test**

```python
# tests/prep/test_lts_ingest.py
import shutil
from pathlib import Path

import pytest

from prep.lts.ingest import (
    BrokenspokeIngestError,
    IntersectionRecord,
    SegmentRecord,
    ingest_intersections,
    ingest_segments,
)


@pytest.fixture
def results_dir(tmp_path: Path, fixtures_dir: Path) -> Path:
    """Create a fake brokenspoke results dir populated with sample geojson."""
    out = tmp_path / "brokenspoke_results"
    out.mkdir()
    shutil.copy(fixtures_dir / "neighborhood_ways_sample.geojson", out / "neighborhood_ways.geojson")
    shutil.copy(fixtures_dir / "neighborhood_ways_intersections_sample.geojson", out / "neighborhood_ways_intersections.geojson")
    return out


def test_ingest_segments_returns_typed_records(results_dir: Path) -> None:
    records = list(ingest_segments(results_dir))

    assert len(records) == 2
    foster = records[0]
    assert isinstance(foster, SegmentRecord)
    assert foster.osm_id == 12345
    assert foster.name == "W Foster Ave"
    assert foster.lts == 4  # max(ft_lts, tf_lts)
    assert foster.geometry_wkt.startswith("LINESTRING")


def test_ingest_intersections_returns_typed_records(results_dir: Path) -> None:
    records = list(ingest_intersections(results_dir))

    assert len(records) == 2
    big = records[0]
    assert isinstance(big, IntersectionRecord)
    assert big.osm_id == 999001
    assert big.lts_approach == 4
    assert big.geometry_wkt.startswith("POINT")


def test_ingest_segments_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(BrokenspokeIngestError):
        list(ingest_segments(tmp_path))
```

- [ ] **Step 4: Run test to verify it fails**

Run: `pytest tests/prep/test_lts_ingest.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 5: Implement the ingest module**

```python
# prep/lts/ingest.py
"""Ingest brokenspoke-analyzer outputs into typed records.

Property field names (current best guess pending §7.1 #2 verification):
- segments: osm_id, name, ft_lts, tf_lts, highway, speed
- intersections: osm_id, lts_approach, signalized, lanes_crossed

If these don't match brokenspoke's actual output, update the constants below
during the Task 26 smoke run.
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from shapely.geometry import shape


class BrokenspokeIngestError(Exception):
    pass


# Field name constants — update if brokenspoke uses different names.
# Verified during Task 26.
SEG_OSM_ID = "osm_id"
SEG_NAME = "name"
SEG_FT_LTS = "ft_lts"
SEG_TF_LTS = "tf_lts"
SEG_HIGHWAY = "highway"
SEG_SPEED = "speed"

INT_OSM_ID = "osm_id"
INT_LTS_APPROACH = "lts_approach"
INT_SIGNALIZED = "signalized"
INT_LANES_CROSSED = "lanes_crossed"


@dataclass(frozen=True)
class SegmentRecord:
    osm_id: int
    name: str | None
    lts: int  # max(ft_lts, tf_lts) — single LTS per edge for v1
    highway: str | None
    speed: int | None
    geometry_wkt: str
    raw_properties: dict


@dataclass(frozen=True)
class IntersectionRecord:
    osm_id: int
    lts_approach: int
    signalized: bool | None
    lanes_crossed: int | None
    geometry_wkt: str
    raw_properties: dict


def ingest_segments(results_dir: Path) -> Iterator[SegmentRecord]:
    path = results_dir / "neighborhood_ways.geojson"
    if not path.exists():
        raise BrokenspokeIngestError(f"missing: {path}")
    data = json.loads(path.read_text())
    for feat in data.get("features", []):
        props = feat.get("properties", {})
        ft = props.get(SEG_FT_LTS)
        tf = props.get(SEG_TF_LTS)
        if ft is None and tf is None:
            continue
        # Take the max of both directions (per Mineta convention) — single LTS per edge.
        # If brokenspoke actually outputs a single 'lts' field, simplify here.
        lts = max(int(ft or 0), int(tf or 0))
        geom = shape(feat["geometry"])
        yield SegmentRecord(
            osm_id=int(props[SEG_OSM_ID]),
            name=props.get(SEG_NAME),
            lts=lts,
            highway=props.get(SEG_HIGHWAY),
            speed=props.get(SEG_SPEED),
            geometry_wkt=geom.wkt,
            raw_properties=props,
        )


def ingest_intersections(results_dir: Path) -> Iterator[IntersectionRecord]:
    path = results_dir / "neighborhood_ways_intersections.geojson"
    if not path.exists():
        raise BrokenspokeIngestError(f"missing: {path}")
    data = json.loads(path.read_text())
    for feat in data.get("features", []):
        props = feat.get("properties", {})
        if INT_LTS_APPROACH not in props:
            continue
        geom = shape(feat["geometry"])
        yield IntersectionRecord(
            osm_id=int(props[INT_OSM_ID]),
            lts_approach=int(props[INT_LTS_APPROACH]),
            signalized=props.get(INT_SIGNALIZED),
            lanes_crossed=props.get(INT_LANES_CROSSED),
            geometry_wkt=geom.wkt,
            raw_properties=props,
        )
```

- [ ] **Step 6: Run test to verify it passes**

Run: `pytest tests/prep/test_lts_ingest.py -v`
Expected: PASS (3 tests)

- [ ] **Step 7: Commit**

```bash
git add chicago-bike-advocacy-map/prep/lts/ingest.py chicago-bike-advocacy-map/tests/prep/test_lts_ingest.py chicago-bike-advocacy-map/tests/fixtures/neighborhood_ways_sample.geojson chicago-bike-advocacy-map/tests/fixtures/neighborhood_ways_intersections_sample.geojson
git commit -m "feat(bikemap): ingest brokenspoke segment + intersection LTS"
```

---

## Task 13: Brokenspoke POI Ingest (TDD)

**Files:**
- Extend: `chicago-bike-advocacy-map/prep/lts/ingest.py`
- Test: extend `chicago-bike-advocacy-map/tests/prep/test_lts_ingest.py`
- Create: `chicago-bike-advocacy-map/tests/fixtures/neighborhood_schools_sample.geojson`

Per spec §3.3, brokenspoke also emits POI exports (schools, parks, hospitals, supermarkets, transit). We ingest them as candidate POIs and write them into the same pipeline as the CDP POIs.

- [ ] **Step 1: Create fixture: neighborhood_schools_sample.geojson**

```json
{
  "type": "FeatureCollection",
  "features": [
    {
      "type": "Feature",
      "properties": {
        "osm_id": 555001,
        "name": "Audubon Elementary School",
        "amenity": "school"
      },
      "geometry": {
        "type": "Point",
        "coordinates": [-87.683, 41.945]
      }
    }
  ]
}
```

- [ ] **Step 2: Add the failing POI test to test_lts_ingest.py**

Append to `tests/prep/test_lts_ingest.py`:

```python
import shutil
from prep.lts.ingest import PoiRecord, ingest_brokenspoke_pois


def test_ingest_brokenspoke_pois_categorizes_by_filename(
    tmp_path: Path, fixtures_dir: Path
) -> None:
    out = tmp_path / "brokenspoke_results"
    out.mkdir()
    # Copy schools fixture as 'neighborhood_schools.geojson'
    shutil.copy(fixtures_dir / "neighborhood_schools_sample.geojson", out / "neighborhood_schools.geojson")
    # Use the same fixture content for hospitals to test category mapping
    shutil.copy(fixtures_dir / "neighborhood_schools_sample.geojson", out / "neighborhood_hospitals.geojson")

    records = list(ingest_brokenspoke_pois(out))

    assert len(records) == 2
    assert {r.category for r in records} == {"school", "hospital"}
    school_rec = next(r for r in records if r.category == "school")
    assert school_rec.name == "Audubon Elementary School"
    assert school_rec.geometry_wkt.startswith("POINT")
    assert school_rec.source == "brokenspoke"


def test_ingest_brokenspoke_pois_skips_unknown_files(tmp_path: Path) -> None:
    out = tmp_path / "brokenspoke_results"
    out.mkdir()
    (out / "neighborhood_unknown_file.geojson").write_text(
        '{"type":"FeatureCollection","features":[]}'
    )
    records = list(ingest_brokenspoke_pois(out))
    assert records == []


def test_ingest_brokenspoke_pois_composes_osm_address_from_components(
    tmp_path: Path,
) -> None:
    """OSM POIs typically have addr:housenumber + addr:street + addr:city
    instead of a single addr:full field. Address must be composed from these.
    """
    out = tmp_path / "brokenspoke_results"
    out.mkdir()
    (out / "neighborhood_schools.geojson").write_text(
        '''{"type":"FeatureCollection","features":[
          {"type":"Feature",
           "properties":{
             "osm_id":1,
             "name":"Audubon Elementary",
             "addr:housenumber":"3500",
             "addr:street":"N Hoyne Ave",
             "addr:city":"Chicago"
           },
           "geometry":{"type":"Point","coordinates":[-87.683, 41.945]}
          },
          {"type":"Feature",
           "properties":{
             "osm_id":2,
             "name":"Solo Street School",
             "addr:street":"N Lincoln Ave"
           },
           "geometry":{"type":"Point","coordinates":[-87.680, 41.940]}
          },
          {"type":"Feature",
           "properties":{
             "osm_id":3,
             "name":"Unknown Address School"
           },
           "geometry":{"type":"Point","coordinates":[-87.679, 41.939]}
          }
        ]}'''
    )
    records = list(ingest_brokenspoke_pois(out))
    addrs = {r.name: r.address for r in records}
    assert addrs["Audubon Elementary"] == "3500 N Hoyne Ave, Chicago"
    assert addrs["Solo Street School"] == "N Lincoln Ave"
    assert addrs["Unknown Address School"] is None
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/prep/test_lts_ingest.py -v`
Expected: FAIL — `ImportError: cannot import name 'PoiRecord'`.

- [ ] **Step 4: Extend the ingest module**

Append to `prep/lts/ingest.py`:

```python
# Filename → POI category mapping. Update if brokenspoke uses different filenames.
BROKENSPOKE_POI_FILES: dict[str, str] = {
    "neighborhood_schools.geojson": "school",
    "neighborhood_hospitals.geojson": "hospital",
    "neighborhood_parks.geojson": "park",
    "neighborhood_supermarkets.geojson": "grocery",
    "neighborhood_transit.geojson": "transit",
    "neighborhood_pharmacies.geojson": "pharmacy",
    "neighborhood_doctors.geojson": "doctor",
    "neighborhood_dentists.geojson": "dentist",
    "neighborhood_universities.geojson": "university",
    "neighborhood_colleges.geojson": "college",
    "neighborhood_community_centers.geojson": "community_center",
    "neighborhood_social_services.geojson": "social_services",
    "neighborhood_retail.geojson": "retail",
}


@dataclass(frozen=True)
class PoiRecord:
    name: str | None
    category: str
    address: str | None
    geometry_wkt: str
    source: str  # "brokenspoke" or "cdp"
    raw_properties: dict


def _compose_osm_address(props: dict) -> str | None:
    """Build a human-readable address string from OSM address tags.

    OSM uses several conventions:
      - addr:full — single string (rare)
      - addr:housenumber + addr:street + addr:city — most common
      - address — bare 'address' field (some imports)

    Returns the first that yields a non-empty string, else None.
    """
    full = props.get("addr:full") or props.get("address")
    if full:
        return str(full).strip() or None

    housenumber = props.get("addr:housenumber")
    street = props.get("addr:street")
    city = props.get("addr:city")

    if not (housenumber or street):
        return None

    parts = [str(housenumber).strip() if housenumber else None,
             str(street).strip() if street else None]
    line1 = " ".join(p for p in parts if p)
    if not line1:
        return None
    if city:
        return f"{line1}, {str(city).strip()}"
    return line1


def ingest_brokenspoke_pois(results_dir: Path) -> Iterator[PoiRecord]:
    """Walk all known brokenspoke POI files and yield PoiRecords."""
    for filename, category in BROKENSPOKE_POI_FILES.items():
        path = results_dir / filename
        if not path.exists():
            continue
        data = json.loads(path.read_text())
        for feat in data.get("features", []):
            props = feat.get("properties", {})
            geom = shape(feat["geometry"])
            yield PoiRecord(
                name=props.get("name"),
                category=category,
                address=_compose_osm_address(props),
                geometry_wkt=geom.wkt,
                source="brokenspoke",
                raw_properties=props,
            )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/prep/test_lts_ingest.py -v`
Expected: PASS (6 tests total — base 3 + POI base + POI skip + OSM address composition)

- [ ] **Step 6: Commit**

```bash
git add chicago-bike-advocacy-map/prep/lts/ingest.py chicago-bike-advocacy-map/tests/prep/test_lts_ingest.py chicago-bike-advocacy-map/tests/fixtures/neighborhood_schools_sample.geojson
git commit -m "feat(bikemap): ingest brokenspoke POI exports + OSM address composition"
```

---

## Task 14: HIN-to-OSM Spatial Join (TDD)

**Files:**
- Create: `chicago-bike-advocacy-map/prep/joins/hin_to_osm.py`
- Test: `chicago-bike-advocacy-map/tests/prep/test_hin_to_osm.py`

Per spec §3.12: HIN segments join to OSM segments via 10m buffer + ±30° bearing match. HIN intersections join to OSM intersection nodes via 30m nearest-neighbor.

- [ ] **Step 1: Write the failing test**

```python
# tests/prep/test_hin_to_osm.py
from shapely.geometry import LineString, Point

from prep.joins.hin_to_osm import (
    HinIntersectionFeature,
    HinSegmentFeature,
    OsmIntersection,
    OsmSegment,
    join_hin_intersections_to_osm,
    join_hin_segments_to_osm,
)


def test_join_segments_matches_overlapping_parallel_lines() -> None:
    # OSM segment running east-west at y=41.975
    osm = [
        OsmSegment(osm_id=1, geometry=LineString([(-87.689, 41.975), (-87.679, 41.975)])),
    ]
    # HIN segment overlapping the same line, slightly offset (5m N)
    hin = [
        HinSegmentFeature(
            feature_id="h1",
            geometry=LineString([(-87.689, 41.97505), (-87.679, 41.97505)]),
            modal_flags={"bike": True, "ped": True},
            severity_rank=4,
        ),
    ]
    matches = list(join_hin_segments_to_osm(hin_segments=hin, osm_segments=osm))
    assert len(matches) == 1
    assert matches[0].osm_id == 1
    assert matches[0].hin_feature_id == "h1"


def test_join_segments_skips_perpendicular_lines() -> None:
    # OSM east-west, HIN north-south through the same point — perpendicular bearing
    osm = [
        OsmSegment(osm_id=1, geometry=LineString([(-87.689, 41.975), (-87.679, 41.975)])),
    ]
    hin = [
        HinSegmentFeature(
            feature_id="h1",
            geometry=LineString([(-87.684, 41.973), (-87.684, 41.977)]),
            modal_flags={"bike": True, "ped": False},
            severity_rank=3,
        ),
    ]
    matches = list(join_hin_segments_to_osm(hin_segments=hin, osm_segments=osm))
    # Bearings differ by 90 degrees — should NOT match
    assert matches == []


def test_join_intersections_nearest_within_30m() -> None:
    # OSM intersection at exact point
    osm = [OsmIntersection(osm_id=42, geometry=Point(-87.689, 41.975))]
    # HIN intersection ~10m east (0.0001 degrees lng ≈ ~8m at this latitude)
    hin = [
        HinIntersectionFeature(
            feature_id="hi1",
            geometry=Point(-87.6889, 41.975),
            modal_flags={"bike": True, "ped": True},
            severity_rank=5,
        ),
    ]
    matches = list(join_hin_intersections_to_osm(hin_intersections=hin, osm_intersections=osm))
    assert len(matches) == 1
    assert matches[0].osm_id == 42
    assert matches[0].hin_feature_id == "hi1"


def test_join_intersections_skips_far_features() -> None:
    osm = [OsmIntersection(osm_id=42, geometry=Point(-87.689, 41.975))]
    # HIN ~500m away
    hin = [
        HinIntersectionFeature(
            feature_id="hi2",
            geometry=Point(-87.683, 41.975),
            modal_flags={"bike": True, "ped": True},
            severity_rank=3,
        ),
    ]
    matches = list(join_hin_intersections_to_osm(hin_intersections=hin, osm_intersections=osm))
    assert matches == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/prep/test_hin_to_osm.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement the join**

```python
# prep/joins/hin_to_osm.py
from __future__ import annotations

import math
from collections.abc import Iterator
from dataclasses import dataclass

from pyproj import Transformer
from shapely.geometry import LineString, Point
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform

# WGS84 → Illinois state plane (EPSG:3435) for accurate distance/bearing math.
_TO_3435 = Transformer.from_crs("EPSG:4326", "EPSG:3435", always_xy=True).transform
_TO_4326 = Transformer.from_crs("EPSG:3435", "EPSG:4326", always_xy=True).transform

SEG_BUFFER_METERS = 10.0
SEG_BEARING_TOLERANCE_DEG = 30.0
INT_NEAREST_METERS = 30.0


@dataclass(frozen=True)
class OsmSegment:
    osm_id: int
    geometry: LineString  # in EPSG:4326


@dataclass(frozen=True)
class OsmIntersection:
    osm_id: int
    geometry: Point  # in EPSG:4326


@dataclass(frozen=True)
class HinSegmentFeature:
    feature_id: str
    geometry: LineString  # in EPSG:4326
    modal_flags: dict[str, bool]
    severity_rank: int | None


@dataclass(frozen=True)
class HinIntersectionFeature:
    feature_id: str
    geometry: Point  # in EPSG:4326
    modal_flags: dict[str, bool]
    severity_rank: int | None


@dataclass(frozen=True)
class HinSegmentMatch:
    osm_id: int
    hin_feature_id: str
    modal_flags: dict[str, bool]
    severity_rank: int | None


@dataclass(frozen=True)
class HinIntersectionMatch:
    osm_id: int
    hin_feature_id: str
    modal_flags: dict[str, bool]
    severity_rank: int | None


def _project(g: BaseGeometry) -> BaseGeometry:
    return transform(_TO_3435, g)


def _bearing(line: LineString, near_point: Point | None = None) -> float:
    """Return bearing in degrees of a (projected) LineString.

    For curved or multi-segment lines, the start→end chord can be misleading.
    If `near_point` is provided, returns the bearing of the line's
    sub-segment closest to that point. Otherwise falls back to start→end.

    Bearings returned mod 180° (bidirectional — direction-independent for
    matching against HIN features that may be digitized either way).
    """
    coords = list(line.coords)
    if len(coords) < 2:
        return 0.0

    if near_point is not None:
        # Find the segment whose midpoint is closest to near_point.
        best_idx = 0
        best_dist = float("inf")
        for i in range(len(coords) - 1):
            mx = (coords[i][0] + coords[i + 1][0]) / 2
            my = (coords[i][1] + coords[i + 1][1]) / 2
            d = (mx - near_point.x) ** 2 + (my - near_point.y) ** 2
            if d < best_dist:
                best_dist = d
                best_idx = i
        x0, y0 = coords[best_idx][:2]
        x1, y1 = coords[best_idx + 1][:2]
    else:
        x0, y0 = coords[0][:2]
        x1, y1 = coords[-1][:2]

    return math.degrees(math.atan2(y1 - y0, x1 - x0)) % 180.0


def _bearing_diff(a: float, b: float) -> float:
    d = abs(a - b) % 180.0
    return min(d, 180.0 - d)


def join_hin_segments_to_osm(
    *,
    hin_segments: list[HinSegmentFeature],
    osm_segments: list[OsmSegment],
) -> Iterator[HinSegmentMatch]:
    """Spatial-join HIN segments to OSM segments by buffer + bearing match.

    Uses an R-tree index (shapely STRtree) for the OSM side to make this
    O((N+M) log N) instead of O(N*M). At Chicago scale (~80k OSM segments,
    ~5k HIN), the naive nested loop is prohibitive (~400M comparisons);
    indexed lookup brings it to a few seconds.
    """
    from shapely.strtree import STRtree

    if not osm_segments:
        return

    osm_proj = [(s, _project(s.geometry)) for s in osm_segments]
    osm_geoms = [g for _, g in osm_proj]
    tree = STRtree(osm_geoms)

    for hin in hin_segments:
        hin_proj = _project(hin.geometry)
        hin_buffered = hin_proj.buffer(SEG_BUFFER_METERS)
        # Use the buffer's centroid as the "near point" for bearing calculation
        # — matches OSM segment bearing at the overlap region rather than chord.
        hin_centroid = hin_proj.centroid
        hin_bearing = _bearing(hin_proj, near_point=hin_centroid)

        # Index lookup with predicate filtering: returns indices of OSM segments
        # whose actual geometry (not just bbox) intersects the buffered HIN.
        # shapely 2.x's STRtree.query(predicate="intersects") performs both the
        # bbox prefilter and the exact predicate test in one call — no need for
        # a separate `osm_geom.intersects(hin_buffered)` check after.
        candidate_idxs = tree.query(hin_buffered, predicate="intersects")

        for idx in candidate_idxs:
            osm, osm_geom = osm_proj[idx]
            osm_bearing = _bearing(osm_geom, near_point=hin_centroid)
            if _bearing_diff(hin_bearing, osm_bearing) > SEG_BEARING_TOLERANCE_DEG:
                continue
            yield HinSegmentMatch(
                osm_id=osm.osm_id,
                hin_feature_id=hin.feature_id,
                modal_flags=hin.modal_flags,
                severity_rank=hin.severity_rank,
            )


def join_hin_intersections_to_osm(
    *,
    hin_intersections: list[HinIntersectionFeature],
    osm_intersections: list[OsmIntersection],
) -> Iterator[HinIntersectionMatch]:
    """Spatial-join HIN intersections to OSM intersection nodes by nearest-neighbor.

    Uses an STRtree-backed nearest-neighbor query for O(log N) lookups.
    """
    from shapely.strtree import STRtree

    if not osm_intersections:
        return

    osm_proj = [(o, _project(o.geometry)) for o in osm_intersections]
    osm_geoms = [g for _, g in osm_proj]
    tree = STRtree(osm_geoms)

    for hin in hin_intersections:
        hin_proj = _project(hin.geometry)
        # Query the single nearest OSM intersection.
        nearest_idx = tree.nearest(hin_proj)
        osm, osm_geom = osm_proj[nearest_idx]
        d = hin_proj.distance(osm_geom)
        if d > INT_NEAREST_METERS:
            continue

        yield HinIntersectionMatch(
            osm_id=osm.osm_id,
            hin_feature_id=hin.feature_id,
            modal_flags=hin.modal_flags,
            severity_rank=hin.severity_rank,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/prep/test_hin_to_osm.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add chicago-bike-advocacy-map/prep/joins/hin_to_osm.py chicago-bike-advocacy-map/tests/prep/test_hin_to_osm.py
git commit -m "feat(bikemap): add HIN-to-OSM spatial join (buffer + bearing)"
```

---

## Task 15: Database Schema (SQL DDL)

**Files:**
- Create: `chicago-bike-advocacy-map/prep/db/schema.sql`
- Create: `chicago-bike-advocacy-map/prep/db/cache_schema.sql`

Per spec §3.2 and §3.5: `bikemap.db` is read-only in production with the main tables; `cache.db` is a separate writable file for `gap_cache`. We declare both schemas here.

- [ ] **Step 1: Write bikemap.db schema**

```sql
-- prep/db/schema.sql
-- Schema for bikemap.db. Read-only in production.
--
-- GEOMETRY STORAGE CONVENTION:
-- - Geometry columns store STANDARD BINARY WKB (Well-Known Binary) blobs in EPSG:4326.
-- - This format is interoperable: shapely.wkb.loads() reads it directly, and
--   SpatiaLite's RecoverGeometryColumn() can register these columns as spatial-indexed
--   Geometry columns at runtime (Plan 2's web service does this on startup).
-- - Plan 1 (this file) does NOT load SpatiaLite — we just write WKB. SpatiaLite-backed
--   spatial queries are a Plan 2 concern.
--
-- Distance math at runtime uses pyproj reprojection to EPSG:3435 (IL state plane).

PRAGMA foreign_keys = ON;

-- Schema versioning. Bump when any table changes shape.
-- Code must be backwards-compatible with the previous 2 schema versions (spec §3.11).
CREATE TABLE IF NOT EXISTS schema_meta (
    schema_version INTEGER NOT NULL,
    built_at TEXT NOT NULL,
    code_version TEXT
);

-- Per-source refresh metadata. One row per source per refresh.
CREATE TABLE IF NOT EXISTS meta (
    source TEXT PRIMARY KEY,
    last_refresh TEXT NOT NULL,
    record_count INTEGER NOT NULL,
    status TEXT NOT NULL  -- "OK" | "WARN" | "FAIL"
);

-- Street segments (edges of the routing graph).
CREATE TABLE IF NOT EXISTS streets (
    osm_id INTEGER PRIMARY KEY,
    name TEXT,
    geom BLOB NOT NULL,           -- WKB LineString (EPSG:4326)
    head_node_osm_id INTEGER,     -- nearest intersection node at start
    tail_node_osm_id INTEGER,     -- nearest intersection node at end
    length_m REAL NOT NULL,
    lts INTEGER NOT NULL,         -- 1..3
    highway TEXT,
    speed INTEGER,
    on_hin INTEGER NOT NULL DEFAULT 0,           -- 0/1 boolean
    hin_modal_bike INTEGER NOT NULL DEFAULT 0,
    hin_modal_ped INTEGER NOT NULL DEFAULT 0,
    hin_severity_rank INTEGER
);

CREATE INDEX IF NOT EXISTS idx_streets_head ON streets(head_node_osm_id);
CREATE INDEX IF NOT EXISTS idx_streets_tail ON streets(tail_node_osm_id);

-- Intersection nodes.
CREATE TABLE IF NOT EXISTS intersections (
    osm_id INTEGER PRIMARY KEY,
    geom BLOB NOT NULL,           -- WKB Point (EPSG:4326)
    lts_approach INTEGER NOT NULL,  -- 1..3
    signalized INTEGER,
    lanes_crossed INTEGER,
    on_hin INTEGER NOT NULL DEFAULT 0,
    hin_modal_bike INTEGER NOT NULL DEFAULT 0,
    hin_modal_ped INTEGER NOT NULL DEFAULT 0,
    hin_severity_rank INTEGER
);

-- Raw HIN feature mirror (for reference / debugging).
CREATE TABLE IF NOT EXISTS hin_features (
    feature_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,           -- "segment" | "intersection"
    modal_bike INTEGER NOT NULL DEFAULT 0,
    modal_ped INTEGER NOT NULL DEFAULT 0,
    severity_rank INTEGER,
    source_geom BLOB NOT NULL     -- original HIN geometry (WKB)
);

-- Points of interest. Sourced from brokenspoke or CDP.
CREATE TABLE IF NOT EXISTS pois (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    address TEXT,
    category TEXT NOT NULL,       -- "school" | "park" | "grocery" | "hospital" | "alderman" | "library" | "transit" | ...
    source TEXT NOT NULL,         -- "brokenspoke" | "cdp"
    geom BLOB NOT NULL            -- WKB Point (EPSG:4326)
);

CREATE INDEX IF NOT EXISTS idx_pois_category ON pois(category);

-- Treatment library content (loaded from treatments/*.md).
CREATE TABLE IF NOT EXISTS treatments (
    slug TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    ward TEXT,
    location_lat REAL,
    location_lng REAL,
    photo_path TEXT,
    source_url TEXT,
    summary TEXT,
    body_md TEXT NOT NULL
);
```

- [ ] **Step 2: Write cache.db schema**

```sql
-- prep/db/cache_schema.sql
-- Schema for cache.db. Read-write at runtime; created on first cache miss.
-- Reset whenever bikemap.db changes (web service detects schema_version+record_count fingerprint mismatch).

CREATE TABLE IF NOT EXISTS cache_fingerprint (
    bikemap_schema_version INTEGER NOT NULL,
    bikemap_streets_count INTEGER NOT NULL,
    built_against_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS gap_cache (
    cache_key TEXT PRIMARY KEY,    -- SHA-256 of (home_coord_rounded, dest_coord_rounded, tier)
    result_json TEXT NOT NULL,
    computed_at TEXT NOT NULL,
    size_bytes INTEGER NOT NULL    -- for LRU eviction
);

CREATE INDEX IF NOT EXISTS idx_gap_cache_computed_at ON gap_cache(computed_at);
```

- [ ] **Step 3: Commit**

```bash
git add chicago-bike-advocacy-map/prep/db/schema.sql chicago-bike-advocacy-map/prep/db/cache_schema.sql
git commit -m "feat(bikemap): add bikemap.db and cache.db SQL schemas"
```

---

## Task 16: DB Builder (TDD)

**Files:**
- Create: `chicago-bike-advocacy-map/prep/db/builder.py`
- Test: `chicago-bike-advocacy-map/tests/prep/test_db_builder.py`

The DB builder takes typed records (SegmentRecord, IntersectionRecord, PoiRecord, etc.) and writes them to `bikemap.db`. Single write entry point; no direct DB writes elsewhere.

- [ ] **Step 1: Write the failing test**

```python
# tests/prep/test_db_builder.py
import sqlite3
from pathlib import Path

from prep.db.builder import (
    SCHEMA_VERSION,
    DbBuilder,
    bytes_to_wkt,
)
from prep.lts.ingest import IntersectionRecord, PoiRecord, SegmentRecord


def test_builder_creates_schema_and_writes_streets(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    builder = DbBuilder(db_path)
    builder.create_schema()

    seg = SegmentRecord(
        osm_id=12345,
        name="W Foster Ave",
        lts=4,
        highway="primary",
        speed=30,
        geometry_wkt="LINESTRING(-87.689 41.975, -87.679 41.975)",
        raw_properties={},
    )
    builder.insert_streets([seg])
    builder.close()

    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT osm_id, name, lts, length_m FROM streets").fetchall()
    assert len(rows) == 1
    assert rows[0][0] == 12345
    assert rows[0][1] == "W Foster Ave"
    assert rows[0][2] == 4
    assert rows[0][3] > 0  # length computed


def test_builder_writes_intersections(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    builder = DbBuilder(db_path)
    builder.create_schema()

    inter = IntersectionRecord(
        osm_id=999001,
        lts_approach=4,
        signalized=True,
        lanes_crossed=6,
        geometry_wkt="POINT(-87.689 41.975)",
        raw_properties={},
    )
    builder.insert_intersections([inter])
    builder.close()

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT osm_id, lts_approach, signalized FROM intersections"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0] == (999001, 4, 1)


def test_builder_writes_pois(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    builder = DbBuilder(db_path)
    builder.create_schema()

    poi = PoiRecord(
        name="Audubon Elementary",
        category="school",
        address=None,
        geometry_wkt="POINT(-87.683 41.945)",
        source="brokenspoke",
        raw_properties={},
    )
    builder.insert_pois([poi])
    builder.close()

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT name, category, source FROM pois"
    ).fetchall()
    assert rows == [("Audubon Elementary", "school", "brokenspoke")]


def test_builder_records_meta(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    builder = DbBuilder(db_path)
    builder.create_schema()
    builder.record_meta("hin", record_count=42, status="OK")
    builder.record_schema_meta(code_version="0.1.0")
    builder.close()

    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT source, record_count, status FROM meta").fetchall()
    assert rows == [("hin", 42, "OK")]
    sm = conn.execute("SELECT schema_version FROM schema_meta").fetchall()
    assert sm == [(SCHEMA_VERSION,)]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/prep/test_db_builder.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement the builder**

```python
# prep/db/builder.py
from __future__ import annotations

import datetime as dt
import sqlite3
from collections.abc import Iterable
from pathlib import Path

from pyproj import Transformer
from shapely import wkb, wkt
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform

from prep.joins.hin_to_osm import HinIntersectionMatch, HinSegmentMatch
from prep.lts.ingest import IntersectionRecord, PoiRecord, SegmentRecord

SCHEMA_VERSION = 1
SCHEMA_SQL_PATH = Path(__file__).parent / "schema.sql"

_TO_3435 = Transformer.from_crs("EPSG:4326", "EPSG:3435", always_xy=True).transform


def _length_meters(g: BaseGeometry) -> float:
    return transform(_TO_3435, g).length


def _to_wkb(g: BaseGeometry) -> bytes:
    """Serialize a shapely geometry to standard binary WKB."""
    return wkb.dumps(g)


def bytes_to_wkt(blob: bytes) -> str:
    """Test helper: read stored WKB and return WKT for inspection."""
    return wkb.loads(blob).wkt


class DbBuilder:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.conn: sqlite3.Connection | None = None

    def _conn(self) -> sqlite3.Connection:
        if self.conn is None:
            self.conn = sqlite3.connect(self.db_path)
            self.conn.execute("PRAGMA foreign_keys = ON")
        return self.conn

    def create_schema(self) -> None:
        sql = SCHEMA_SQL_PATH.read_text()
        self._conn().executescript(sql)

    def insert_streets(
        self,
        segments: Iterable[SegmentRecord],
        hin_matches: dict[int, HinSegmentMatch] | None = None,
        head_tail: dict[int, tuple[int | None, int | None]] | None = None,
    ) -> int:
        """Insert street segments.

        head_tail: optional mapping osm_id -> (head_node_osm_id, tail_node_osm_id).
        If None, head/tail are stored as NULL — caller (orchestrator) is expected
        to compute these via the graph-linking step (Task 16a) and pass them in.
        """
        hin_matches = hin_matches or {}
        head_tail = head_tail or {}
        rows = []
        for s in segments:
            geom = wkt.loads(s.geometry_wkt)
            length = _length_meters(geom)
            m = hin_matches.get(s.osm_id)
            head, tail = head_tail.get(s.osm_id, (None, None))
            rows.append((
                s.osm_id,
                s.name,
                _to_wkb(geom),
                head,
                tail,
                length,
                s.lts,
                s.highway,
                s.speed,
                1 if m else 0,
                1 if m and m.modal_flags.get("bike") else 0,
                1 if m and m.modal_flags.get("ped") else 0,
                m.severity_rank if m else None,
            ))
        cur = self._conn().executemany(
            "INSERT OR REPLACE INTO streets "
            "(osm_id, name, geom, head_node_osm_id, tail_node_osm_id, length_m, "
            "lts, highway, speed, on_hin, hin_modal_bike, hin_modal_ped, hin_severity_rank) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        self._conn().commit()
        return cur.rowcount

    def insert_intersections(
        self,
        intersections: Iterable[IntersectionRecord],
        hin_matches: dict[int, HinIntersectionMatch] | None = None,
    ) -> int:
        hin_matches = hin_matches or {}
        rows = []
        for i in intersections:
            geom = wkt.loads(i.geometry_wkt)
            m = hin_matches.get(i.osm_id)
            rows.append((
                i.osm_id,
                _to_wkb(geom),
                i.lts_approach,
                1 if i.signalized else (0 if i.signalized is False else None),
                i.lanes_crossed,
                1 if m else 0,
                1 if m and m.modal_flags.get("bike") else 0,
                1 if m and m.modal_flags.get("ped") else 0,
                m.severity_rank if m else None,
            ))
        cur = self._conn().executemany(
            "INSERT OR REPLACE INTO intersections "
            "(osm_id, geom, lts_approach, signalized, lanes_crossed, "
            "on_hin, hin_modal_bike, hin_modal_ped, hin_severity_rank) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            rows,
        )
        self._conn().commit()
        return cur.rowcount

    def insert_pois(self, pois: Iterable[PoiRecord]) -> int:
        rows = [
            (p.name, p.address, p.category, p.source, _to_wkb(wkt.loads(p.geometry_wkt)))
            for p in pois
        ]
        cur = self._conn().executemany(
            "INSERT INTO pois (name, address, category, source, geom) "
            "VALUES (?,?,?,?,?)",
            rows,
        )
        self._conn().commit()
        return cur.rowcount

    def insert_treatments(
        self, rows: Iterable[tuple],
    ) -> int:
        """Insert treatment rows (tuples) into the treatments table.

        Each row must be: (slug, type, ward, location_lat, location_lng,
        photo_path, source_url, summary, body_md). The treatments_loader
        parses markdown and constructs these tuples — this method is the
        single write entry point for the treatments table (no private DB
        access from outside DbBuilder).
        """
        rows = list(rows)
        cur = self._conn().executemany(
            "INSERT OR REPLACE INTO treatments "
            "(slug, type, ward, location_lat, location_lng, photo_path, source_url, summary, body_md) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            rows,
        )
        self._conn().commit()
        return cur.rowcount

    def record_meta(self, source: str, record_count: int, status: str) -> None:
        self._conn().execute(
            "INSERT OR REPLACE INTO meta (source, last_refresh, record_count, status) "
            "VALUES (?,?,?,?)",
            (source, dt.datetime.now(dt.timezone.utc).isoformat(), record_count, status),
        )
        self._conn().commit()

    def record_schema_meta(self, code_version: str) -> None:
        self._conn().execute("DELETE FROM schema_meta")
        self._conn().execute(
            "INSERT INTO schema_meta (schema_version, built_at, code_version) VALUES (?,?,?)",
            (SCHEMA_VERSION, dt.datetime.now(dt.timezone.utc).isoformat(), code_version),
        )
        self._conn().commit()

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()
            self.conn = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/prep/test_db_builder.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add chicago-bike-advocacy-map/prep/db/builder.py chicago-bike-advocacy-map/tests/prep/test_db_builder.py
git commit -m "feat(bikemap): add SQLite DB builder for streets/intersections/pois"
```

---

## Task 16a: Graph Linking — Head/Tail Node Assignment (TDD)

**Files:**
- Create: `chicago-bike-advocacy-map/prep/joins/graph_link.py`
- Test: `chicago-bike-advocacy-map/tests/prep/test_graph_link.py`

For routing in Plan 2, every street segment must know its `head_node_osm_id` and `tail_node_osm_id` (the intersection nodes at each endpoint). Whether brokenspoke emits these directly or not, this task **always computes them by RTree-nearest from segment endpoints to the intersection table**. The compute path is idempotent and fast (~30-60s for Chicago).

If the smoke run (Task 25) reveals brokenspoke also emits `from_node` / `to_node` fields directly, that's a future optimization (skip the compute) — but the compute is the v1 default because it's robust to brokenspoke's output schema changing.

This is a real Plan 1 task — Plan 2 should never run prep work.

- [ ] **Step 1: Write the failing test**

```python
# tests/prep/test_graph_link.py
from prep.joins.graph_link import compute_head_tail_nodes
from shapely.geometry import LineString, Point


def test_compute_head_tail_nodes_links_each_endpoint_to_nearest_intersection() -> None:
    # Three intersections forming a triangle.
    intersections = [
        (101, Point(0.0, 0.0)),
        (102, Point(0.001, 0.0)),  # ~111m east
        (103, Point(0.0005, 0.001)),  # ~111m NE
    ]
    # Two segments connecting them.
    segments = [
        (1, LineString([(0.0, 0.0), (0.001, 0.0)])),  # 101 → 102
        (2, LineString([(0.001, 0.0), (0.0005, 0.001)])),  # 102 → 103
    ]

    head_tail = compute_head_tail_nodes(
        segments=segments, intersections=intersections,
    )

    assert head_tail[1] == (101, 102)
    assert head_tail[2] == (102, 103)


def test_compute_head_tail_nodes_returns_none_when_no_intersection_within_tolerance() -> None:
    intersections = [(101, Point(0.0, 0.0))]
    # Segment endpoints are far from any intersection.
    segments = [(1, LineString([(0.5, 0.5), (0.6, 0.6)]))]

    head_tail = compute_head_tail_nodes(
        segments=segments, intersections=intersections,
        max_distance_m=10.0,
    )
    assert head_tail[1] == (None, None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/prep/test_graph_link.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement the linker**

```python
# prep/joins/graph_link.py
"""Link street segment endpoints to nearby intersection nodes.

Used when brokenspoke-analyzer doesn't emit `from_node`/`to_node` directly.
Pure shapely + STRtree. Output feeds `DbBuilder.insert_streets(head_tail=...)`.
"""
from __future__ import annotations

from pyproj import Transformer
from shapely.geometry import LineString, Point
from shapely.ops import transform
from shapely.strtree import STRtree

_TO_3435 = Transformer.from_crs("EPSG:4326", "EPSG:3435", always_xy=True).transform

# Default tolerance: an OSM segment endpoint should be within 5m of its
# corresponding intersection node. Larger tolerances risk linking to the
# wrong intersection (especially at dense intersections).
DEFAULT_MAX_DISTANCE_M = 5.0


def _project_point(p: Point) -> Point:
    return transform(_TO_3435, p)


def compute_head_tail_nodes(
    *,
    segments: list[tuple[int, LineString]],
    intersections: list[tuple[int, Point]],
    max_distance_m: float = DEFAULT_MAX_DISTANCE_M,
) -> dict[int, tuple[int | None, int | None]]:
    """For each (osm_id, LineString) segment, find the nearest intersection node
    to each endpoint. Return mapping segment_osm_id -> (head_id, tail_id).

    Inputs in EPSG:4326. Distance comparisons in EPSG:3435.
    """
    if not intersections or not segments:
        return {seg_id: (None, None) for seg_id, _ in segments}

    # Project intersection points to state plane.
    int_proj = [(osm_id, _project_point(geom)) for osm_id, geom in intersections]
    int_geoms = [g for _, g in int_proj]
    tree = STRtree(int_geoms)

    out: dict[int, tuple[int | None, int | None]] = {}
    for seg_id, line in segments:
        coords = list(line.coords)
        if len(coords) < 2:
            out[seg_id] = (None, None)
            continue

        head_pt = _project_point(Point(coords[0][:2]))
        tail_pt = _project_point(Point(coords[-1][:2]))

        head_idx = tree.nearest(head_pt)
        tail_idx = tree.nearest(tail_pt)

        head_dist = head_pt.distance(int_geoms[head_idx])
        tail_dist = tail_pt.distance(int_geoms[tail_idx])

        head_id = int_proj[head_idx][0] if head_dist <= max_distance_m else None
        tail_id = int_proj[tail_idx][0] if tail_dist <= max_distance_m else None
        out[seg_id] = (head_id, tail_id)

    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/prep/test_graph_link.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Wire into the orchestrator (preview — full integration in Task 22)**

Add a note here: in `prep/main.py`, between segment ingestion and `insert_streets`, call:

```python
from prep.joins.graph_link import compute_head_tail_nodes
from shapely import wkt as _wkt

head_tail_input = [
    (s.osm_id, _wkt.loads(s.geometry_wkt))
    for s in segs
]
intersection_input = [
    (i.osm_id, _wkt.loads(i.geometry_wkt))
    for i in ints
]
head_tail_map = compute_head_tail_nodes(
    segments=head_tail_input,
    intersections=intersection_input,
)

builder.insert_streets(segs, hin_matches=seg_match_map, head_tail=head_tail_map)
```

(This is integrated into Task 22's orchestrator code.)

- [ ] **Step 6: Commit**

```bash
git add chicago-bike-advocacy-map/prep/joins/graph_link.py chicago-bike-advocacy-map/tests/prep/test_graph_link.py
git commit -m "feat(bikemap): add graph-linking for street endpoint → intersection node"
```

---

## Task 17: Treatments Markdown Loader (TDD)

**Files:**
- Create: `chicago-bike-advocacy-map/prep/db/treatments_loader.py`
- Test: `chicago-bike-advocacy-map/tests/prep/test_treatments_loader.py`

Loads treatment markdown files (frontmatter + body) into the `treatments` table.

- [ ] **Step 1: Write the failing test**

```python
# tests/prep/test_treatments_loader.py
import sqlite3
from pathlib import Path

from prep.db.builder import DbBuilder
from prep.db.treatments_loader import load_treatments


def test_load_treatments_from_markdown_directory(tmp_path: Path) -> None:
    treatments_dir = tmp_path / "treatments"
    treatments_dir.mkdir()
    (treatments_dir / "pedestrian-refuge.md").write_text(
        """---
type: intersection_treatment
ward: 47
location_lat: 41.945
location_lng: -87.683
photo_path: photos/foster-refuge.jpg
source_url: https://example.com/refuge
summary: Concrete median refuge enabling two-stage crossings.
---

# Pedestrian Refuge Island

Concrete median that gives crossing pedestrians and cyclists a place to stop
in the middle of a wide street, breaking the crossing into two stages.

## Chicago example

Foster Ave & Hoyne Ave, Ward 47.
"""
    )
    (treatments_dir / "neighborhood-greenway.md").write_text(
        """---
type: corridor_treatment
ward: 1
summary: Calmed residential street prioritizing bicycle traffic.
---

# Neighborhood Greenway

A residential street with reduced auto traffic and added bike priority elements.
"""
    )

    db_path = tmp_path / "test.db"
    builder = DbBuilder(db_path)
    builder.create_schema()

    n = load_treatments(treatments_dir, builder)
    builder.close()
    assert n == 2

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT slug, type, ward, location_lat, location_lng, photo_path FROM treatments ORDER BY slug"
    ).fetchall()
    assert len(rows) == 2

    refuge = next(r for r in rows if r[0] == "pedestrian-refuge")
    assert refuge[1] == "intersection_treatment"
    assert refuge[2] == "47"
    assert refuge[3] == 41.945
    assert refuge[5] == "photos/foster-refuge.jpg"

    body = conn.execute(
        "SELECT body_md FROM treatments WHERE slug = ?", ("pedestrian-refuge",)
    ).fetchone()
    assert "Pedestrian Refuge Island" in body[0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/prep/test_treatments_loader.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement the loader**

```python
# prep/db/treatments_loader.py
"""Parse treatment markdown files and write rows to the treatments table.

This module owns the markdown parsing concern only. It calls the public
`DbBuilder.insert_treatments` method to write — never touches private
DB internals.
"""
from __future__ import annotations

from pathlib import Path

import frontmatter

from prep.db.builder import DbBuilder


def load_treatments(treatments_dir: Path, builder: DbBuilder) -> int:
    """Load treatments/*.md files into the `treatments` table.

    Returns the number of treatments loaded. Skips files with malformed
    frontmatter, logging a warning rather than failing the whole pipeline.
    """
    if not treatments_dir.exists():
        return 0

    rows = []
    skipped: list[tuple[Path, Exception]] = []
    for md_path in sorted(treatments_dir.glob("*.md")):
        try:
            post = frontmatter.load(md_path)
            slug = md_path.stem
            meta = post.metadata
            rows.append((
                slug,
                meta.get("type", "unknown"),
                str(meta["ward"]) if meta.get("ward") is not None else None,
                float(meta["location_lat"]) if meta.get("location_lat") is not None else None,
                float(meta["location_lng"]) if meta.get("location_lng") is not None else None,
                meta.get("photo_path"),
                meta.get("source_url"),
                meta.get("summary"),
                post.content,
            ))
        except (ValueError, KeyError) as e:
            skipped.append((md_path, e))

    if skipped:
        # Logged at the orchestrator level via prep_report.md; this loader
        # only returns the count of successful loads.
        for path, err in skipped:
            print(f"WARN: skipping malformed treatment {path.name}: {err}")

    if not rows:
        return 0

    return builder.insert_treatments(rows)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/prep/test_treatments_loader.py -v`
Expected: PASS (1 test)

- [ ] **Step 5: Commit**

```bash
git add chicago-bike-advocacy-map/prep/db/treatments_loader.py chicago-bike-advocacy-map/tests/prep/test_treatments_loader.py
git commit -m "feat(bikemap): load treatments markdown into DB"
```

---

## Task 18: Sample Treatment Markdown Files

**Files:**
- Create: `chicago-bike-advocacy-map/treatments/pedestrian-refuge.md`
- Create: `chicago-bike-advocacy-map/treatments/protected-bike-crossing.md`
- Create: `chicago-bike-advocacy-map/treatments/raised-intersection.md`
- Create: `chicago-bike-advocacy-map/treatments/neighborhood-greenway.md`
- Create: `chicago-bike-advocacy-map/treatments/traffic-circle.md`

Five treatment types with frontmatter + body. Photos can be added later (placeholder photo_path values for now).

- [ ] **Step 1: Write all five files**

`treatments/pedestrian-refuge.md`:

```markdown
---
type: intersection_treatment
ward: 47
location_lat: 41.945
location_lng: -87.683
photo_path: photos/pedestrian-refuge-placeholder.jpg
source_url: https://nacto.org/publication/urban-bikeway-design-guide/intersection-treatments/median-refuge-island/
summary: A concrete median that gives crossing cyclists and pedestrians a place to stop in the middle of a wide street, splitting the crossing into two stages.
---

# Pedestrian Refuge Island

A concrete median in the middle of a wide arterial that allows people on foot or bike to cross one direction of traffic at a time. Particularly valuable on streets too wide to cross in a single signal phase, or unsignalized crossings of multi-lane arterials.

## Why it helps

- Reduces exposure: half the crossing distance per stage.
- Slows turning vehicles by tightening corner radii.
- Provides shelter from traffic for slower or more cautious users.

## Chicago example

The median refuge at Foster Ave near Hoyne Ave (Ward 47) lets cyclists riding north on Hoyne wait safely in the middle of Foster while crossing in two stages.

## Cost / timeline

Typically a quick-build curb extension or paint-and-bollard installation: weeks to a few months. Permanent concrete construction: 6-12 months and ~$50-150k.
```

`treatments/protected-bike-crossing.md`:

```markdown
---
type: intersection_treatment
ward: 1
location_lat: 41.910
location_lng: -87.694
photo_path: photos/protected-bike-crossing-placeholder.jpg
source_url: https://nacto.org/publication/urban-bikeway-design-guide/intersection-treatments/protected-intersections/
summary: Intersection geometry that physically separates turning cars from cyclists, with bike-specific signal phases.
---

# Protected Bike Crossing / Protected Intersection

A redesigned intersection that uses corner refuges (small concrete islands at each corner) and forward stop bars to physically separate bike traffic from turning vehicles. Often paired with a bike-specific signal phase.

## Why it helps

- Eliminates right-hook conflicts between right-turning vehicles and through-cycling traffic.
- Forces drivers to slow and turn more squarely, improving sight lines.
- Bike signal phase removes conflicts at signal change.

## Chicago example

The protected intersection at Milwaukee Ave & Division Ave (Ward 1) along the Milwaukee Ave protected bike lane corridor.

## Cost / timeline

Quick-build version (paint, bollards, planters): months. Full reconstruction with concrete corner refuges: 12-24 months and $300k-1M+.
```

`treatments/raised-intersection.md`:

```markdown
---
type: intersection_treatment
ward: 33
photo_path: photos/raised-intersection-placeholder.jpg
source_url: https://nacto.org/publication/urban-street-design-guide/intersection-design-elements/intersections/raised-intersections/
summary: An intersection raised to sidewalk grade, slowing all turning traffic and giving pedestrians and cyclists priority.
---

# Raised Intersection

The entire intersection is raised to sidewalk level, with sloped approaches on each leg. Acts as a giant speed table — drivers must slow significantly to enter and exit. Pedestrians and cyclists cross at grade.

## Why it helps

- Forces every approaching driver to slow to ~10-15 mph.
- Eliminates the curb-cut-and-cross experience for vulnerable users.
- Reduces crash severity dramatically.

## Cost / timeline

Typically requires reconstruction; 12-18 months and $200k-500k.
```

`treatments/neighborhood-greenway.md`:

```markdown
---
type: corridor_treatment
ward: 47
photo_path: photos/greenway-placeholder.jpg
source_url: https://nacto.org/publication/urban-bikeway-design-guide/cycle-tracks/bicycle-boulevards/
summary: A residential street redesigned to prioritize bicycle and pedestrian traffic by reducing through-vehicle traffic and lowering speeds.
---

# Neighborhood Greenway

A calmed residential street with traffic-calming elements (speed humps, traffic circles, diverters), reduced speed limits (often 20 mph), and bike priority elements (sharrows, bike-specific signage, bike-priority signals at crossings).

## Why it helps

- Creates an LTS 1 corridor on a parallel street, often just one block from a high-stress arterial.
- Cheap relative to building dedicated bike infrastructure.
- Benefits all neighborhood users, not just cyclists.

## Chicago example

Berteau Ave (Ward 47) functions as a neighborhood greenway parallel to Irving Park Rd, with diverters and traffic circles slowing vehicles.

## Cost / timeline

Quick-build calming (paint, planters, speed humps): months and $50-150k per mile. Permanent infrastructure: 12-24 months and $500k-2M per mile.
```

`treatments/traffic-circle.md`:

```markdown
---
type: intersection_treatment
photo_path: photos/traffic-circle-placeholder.jpg
source_url: https://nacto.org/publication/urban-street-design-guide/intersection-design-elements/intersection-control/mini-roundabouts/
summary: A small circular feature in the center of an intersection that slows all turning traffic and reduces conflict points.
---

# Traffic Circle / Mini-Roundabout

A small circular landscaped feature in the center of an intersection. Drivers must slow and turn around it. Eliminates left-turn-across-traffic conflicts and reduces speeds.

## Why it helps

- Replaces stop signs that drivers often roll through.
- Eliminates left-turn-across-oncoming-traffic conflicts.
- Forces all approaches to slow to ~15 mph.
- Quieter than four-way stops (no acceleration from stop).

## Cost / timeline

Concrete-and-landscaping installation: 3-6 months and $30-100k per intersection.
```

- [ ] **Step 2: Verify the treatments loader picks them up**

Run: `pytest tests/prep/test_treatments_loader.py -v`
Expected: still PASS (the existing test uses its own tmp dir, not the real `treatments/`).

- [ ] **Step 3: Commit**

```bash
git add chicago-bike-advocacy-map/treatments/
git commit -m "feat(bikemap): add 5 sample treatment markdown entries"
```

---

## Task 19: LTS Regression Diff Reporter (TDD)

**Files:**
- Create: `chicago-bike-advocacy-map/prep/reporting/lts_diff.py`
- Test: `chicago-bike-advocacy-map/tests/prep/test_lts_diff.py`

Per spec §3.13: each prep run diffs current per-segment LTS scores vs. last run's. Surfaces unexpected churn (e.g., 200 segments dropped from LTS 2 to LTS 3 due to a tag interpretation bug).

- [ ] **Step 1: Write the failing test**

```python
# tests/prep/test_lts_diff.py
import sqlite3
from pathlib import Path

from prep.db.builder import DbBuilder
from prep.lts.ingest import SegmentRecord
from prep.reporting.lts_diff import diff_lts_against_previous


def _build_db_with_segments(db_path: Path, segments: list[tuple[int, int]]) -> None:
    """Build a minimal DB with given (osm_id, lts) pairs."""
    builder = DbBuilder(db_path)
    builder.create_schema()
    recs = [
        SegmentRecord(
            osm_id=osm_id,
            name=None,
            lts=lts,
            highway=None,
            speed=None,
            geometry_wkt="LINESTRING(0 0, 1 0)",
            raw_properties={},
        )
        for osm_id, lts in segments
    ]
    builder.insert_streets(recs)
    builder.close()


def test_lts_diff_no_previous_db_returns_empty_diff(tmp_path: Path) -> None:
    current = tmp_path / "current.db"
    _build_db_with_segments(current, [(1, 1), (2, 2), (3, 4)])

    diff = diff_lts_against_previous(current_db=current, previous_db=tmp_path / "missing.db")
    assert diff.total_segments == 3
    assert diff.changed == []
    assert diff.added == [1, 2, 3]
    assert diff.removed == []


def test_lts_diff_detects_lts_changes(tmp_path: Path) -> None:
    previous = tmp_path / "prev.db"
    current = tmp_path / "curr.db"
    _build_db_with_segments(previous, [(1, 2), (2, 3), (3, 4)])
    _build_db_with_segments(current, [(1, 4), (2, 3), (4, 1)])

    diff = diff_lts_against_previous(current_db=current, previous_db=previous)
    assert diff.total_segments == 3
    # Segment 1: LTS 2 → 4 (changed)
    assert (1, 2, 4) in diff.changed
    # Segment 3: removed
    assert diff.removed == [3]
    # Segment 4: added
    assert diff.added == [4]
    # Segment 2: unchanged — should not appear in changed/added/removed
    assert all(c[0] != 2 for c in diff.changed)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/prep/test_lts_diff.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement the differ**

```python
# prep/reporting/lts_diff.py
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class LtsDiff:
    total_segments: int
    changed: list[tuple[int, int, int]] = field(default_factory=list)  # (osm_id, prev_lts, curr_lts)
    added: list[int] = field(default_factory=list)
    removed: list[int] = field(default_factory=list)

    def to_markdown(self) -> str:
        lines = [
            "# LTS Regression Diff",
            "",
            f"- Total segments in current run: **{self.total_segments}**",
            f"- LTS changed (vs previous): **{len(self.changed)}**",
            f"- New segments: **{len(self.added)}**",
            f"- Removed segments: **{len(self.removed)}**",
            "",
        ]
        if self.changed:
            buckets: dict[tuple[int, int], int] = {}
            for _, prev, curr in self.changed:
                buckets[(prev, curr)] = buckets.get((prev, curr), 0) + 1
            lines.append("## LTS transitions")
            lines.append("")
            lines.append("| Previous LTS | Current LTS | Count |")
            lines.append("|---|---|---|")
            for (p, c), n in sorted(buckets.items()):
                lines.append(f"| {p} | {c} | {n} |")
            lines.append("")
        return "\n".join(lines)


def _load_lts_map(db_path: Path) -> dict[int, int]:
    if not db_path.exists():
        return {}
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT osm_id, lts FROM streets").fetchall()
    finally:
        conn.close()
    return {osm_id: lts for osm_id, lts in rows}


def diff_lts_against_previous(*, current_db: Path, previous_db: Path) -> LtsDiff:
    curr = _load_lts_map(current_db)
    prev = _load_lts_map(previous_db)

    changed: list[tuple[int, int, int]] = []
    added: list[int] = []
    removed: list[int] = []

    for osm_id, curr_lts in curr.items():
        if osm_id not in prev:
            added.append(osm_id)
        elif prev[osm_id] != curr_lts:
            changed.append((osm_id, prev[osm_id], curr_lts))

    for osm_id in prev:
        if osm_id not in curr:
            removed.append(osm_id)

    return LtsDiff(
        total_segments=len(curr),
        changed=sorted(changed),
        added=sorted(added),
        removed=sorted(removed),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/prep/test_lts_diff.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add chicago-bike-advocacy-map/prep/reporting/lts_diff.py chicago-bike-advocacy-map/tests/prep/test_lts_diff.py
git commit -m "feat(bikemap): add LTS regression diff reporter"
```

---

## Task 20: HIN Match Report (TDD)

**Files:**
- Create: `chicago-bike-advocacy-map/prep/reporting/hin_match_report.py`
- Test: `chicago-bike-advocacy-map/tests/prep/test_hin_match_report.py`

Per spec §3.12 + §6.4 launch criterion #2: emit a report listing HIN features that didn't match any OSM feature, plus a coverage percentage. Launch requires ≥ 95% match rate.

- [ ] **Step 1: Write the failing test**

```python
# tests/prep/test_hin_match_report.py
from prep.joins.hin_to_osm import (
    HinIntersectionFeature,
    HinIntersectionMatch,
    HinSegmentFeature,
    HinSegmentMatch,
)
from prep.reporting.hin_match_report import build_hin_match_report
from shapely.geometry import LineString, Point


def test_match_report_summarizes_coverage() -> None:
    hin_segs = [
        HinSegmentFeature(feature_id="s1", geometry=LineString([(0,0),(1,0)]), modal_flags={"bike": True, "ped": False}, severity_rank=4),
        HinSegmentFeature(feature_id="s2", geometry=LineString([(0,1),(1,1)]), modal_flags={"bike": False, "ped": True}, severity_rank=3),
        HinSegmentFeature(feature_id="s3", geometry=LineString([(0,2),(1,2)]), modal_flags={"bike": True, "ped": True}, severity_rank=5),
    ]
    hin_ints = [
        HinIntersectionFeature(feature_id="i1", geometry=Point(0,0), modal_flags={"bike": True, "ped": True}, severity_rank=5),
        HinIntersectionFeature(feature_id="i2", geometry=Point(0,5), modal_flags={"bike": True, "ped": True}, severity_rank=4),
    ]
    seg_matches = [
        HinSegmentMatch(osm_id=11, hin_feature_id="s1", modal_flags={"bike": True, "ped": False}, severity_rank=4),
        HinSegmentMatch(osm_id=12, hin_feature_id="s2", modal_flags={"bike": False, "ped": True}, severity_rank=3),
    ]
    int_matches = [
        HinIntersectionMatch(osm_id=21, hin_feature_id="i1", modal_flags={"bike": True, "ped": True}, severity_rank=5),
    ]

    report = build_hin_match_report(
        hin_segments=hin_segs,
        hin_intersections=hin_ints,
        segment_matches=seg_matches,
        intersection_matches=int_matches,
    )

    # 2/3 segments matched, 1/2 intersections matched
    # Total: 3/5 = 60%
    assert report.segment_match_pct == pytest.approx(2/3 * 100)
    assert report.intersection_match_pct == pytest.approx(50.0)
    assert report.unmatched_segment_ids == ["s3"]
    assert report.unmatched_intersection_ids == ["i2"]

    md = report.to_markdown()
    assert "60" in md or "0.60" in md or "5" in md  # combined coverage somewhere
    assert "s3" in md
    assert "i2" in md
```

Add `import pytest` at the top of the test file.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/prep/test_hin_match_report.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement the report**

```python
# prep/reporting/hin_match_report.py
from __future__ import annotations

from dataclasses import dataclass, field

from prep.joins.hin_to_osm import (
    HinIntersectionFeature,
    HinIntersectionMatch,
    HinSegmentFeature,
    HinSegmentMatch,
)


@dataclass(frozen=True)
class HinMatchReport:
    total_segments: int
    matched_segments: int
    total_intersections: int
    matched_intersections: int
    unmatched_segment_ids: list[str] = field(default_factory=list)
    unmatched_intersection_ids: list[str] = field(default_factory=list)

    @property
    def segment_match_pct(self) -> float:
        if self.total_segments == 0:
            return 100.0
        return 100.0 * self.matched_segments / self.total_segments

    @property
    def intersection_match_pct(self) -> float:
        if self.total_intersections == 0:
            return 100.0
        return 100.0 * self.matched_intersections / self.total_intersections

    @property
    def overall_match_pct(self) -> float:
        total = self.total_segments + self.total_intersections
        if total == 0:
            return 100.0
        return 100.0 * (self.matched_segments + self.matched_intersections) / total

    def to_markdown(self) -> str:
        lines = [
            "# HIN Match Report",
            "",
            f"- Segment match rate: **{self.matched_segments}/{self.total_segments} "
            f"({self.segment_match_pct:.1f}%)**",
            f"- Intersection match rate: **{self.matched_intersections}/{self.total_intersections} "
            f"({self.intersection_match_pct:.1f}%)**",
            f"- Overall match rate: **{self.overall_match_pct:.1f}%**",
            "",
            "Launch criterion (spec §6.4 #2): ≥ 95% overall match rate.",
            "",
        ]
        if self.unmatched_segment_ids:
            lines.append("## Unmatched HIN segments")
            lines.append("")
            for fid in self.unmatched_segment_ids:
                lines.append(f"- `{fid}`")
            lines.append("")
        if self.unmatched_intersection_ids:
            lines.append("## Unmatched HIN intersections")
            lines.append("")
            for fid in self.unmatched_intersection_ids:
                lines.append(f"- `{fid}`")
            lines.append("")
        return "\n".join(lines)


def build_hin_match_report(
    *,
    hin_segments: list[HinSegmentFeature],
    hin_intersections: list[HinIntersectionFeature],
    segment_matches: list[HinSegmentMatch],
    intersection_matches: list[HinIntersectionMatch],
) -> HinMatchReport:
    matched_seg_ids = {m.hin_feature_id for m in segment_matches}
    matched_int_ids = {m.hin_feature_id for m in intersection_matches}

    return HinMatchReport(
        total_segments=len(hin_segments),
        matched_segments=len(matched_seg_ids),
        total_intersections=len(hin_intersections),
        matched_intersections=len(matched_int_ids),
        unmatched_segment_ids=sorted(
            f.feature_id for f in hin_segments if f.feature_id not in matched_seg_ids
        ),
        unmatched_intersection_ids=sorted(
            f.feature_id for f in hin_intersections if f.feature_id not in matched_int_ids
        ),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/prep/test_hin_match_report.py -v`
Expected: PASS (1 test)

- [ ] **Step 5: Commit**

```bash
git add chicago-bike-advocacy-map/prep/reporting/hin_match_report.py chicago-bike-advocacy-map/tests/prep/test_hin_match_report.py
git commit -m "feat(bikemap): add HIN match coverage report"
```

---

## Task 21: Prep Report Orchestrator (TDD)

**Files:**
- Create: `chicago-bike-advocacy-map/prep/reporting/prep_report.py`
- Test: `chicago-bike-advocacy-map/tests/prep/test_prep_report.py`

The main `prep_report.md` summarizes a full prep run: per-source OK/WARN/FAIL, record-count deltas, and pointers to `lts_diff.md` and `hin_match_report.md`. Per spec §3.9.

- [ ] **Step 1: Write the failing test**

```python
# tests/prep/test_prep_report.py
import datetime as dt
from pathlib import Path

from prep.fetchers.base import FetchResult
from prep.reporting.prep_report import (
    SourceRunSummary,
    build_prep_report,
)


def test_prep_report_includes_per_source_status_and_deltas(tmp_path: Path) -> None:
    runs = [
        SourceRunSummary(
            name="hin",
            status="OK",
            record_count=1234,
            previous_record_count=1200,
            warnings=[],
        ),
        SourceRunSummary(
            name="cdot_bike_facilities",
            status="WARN",
            record_count=400,
            previous_record_count=420,
            warnings=["3 rows missing geometry"],
        ),
        SourceRunSummary(
            name="brokenspoke",
            status="OK",
            record_count=80000,
            previous_record_count=None,
            warnings=[],
        ),
    ]

    md = build_prep_report(
        run_started_at=dt.datetime(2026, 5, 5, 14, 0, 0, tzinfo=dt.timezone.utc),
        run_finished_at=dt.datetime(2026, 5, 5, 15, 30, 0, tzinfo=dt.timezone.utc),
        sources=runs,
        lts_diff_path=tmp_path / "lts_diff.md",
        hin_match_report_path=tmp_path / "hin_match_report.md",
    )

    assert "Prep Report" in md
    assert "2026-05-05" in md
    assert "1234" in md
    assert "+34" in md  # delta for hin
    assert "WARN" in md
    assert "3 rows missing geometry" in md
    assert "first run" in md.lower()  # for brokenspoke (no previous)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/prep/test_prep_report.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement the report**

```python
# prep/reporting/prep_report.py
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SourceRunSummary:
    name: str
    status: str  # "OK" | "WARN" | "FAIL"
    record_count: int
    previous_record_count: int | None
    warnings: list[str]


def build_prep_report(
    *,
    run_started_at: dt.datetime,
    run_finished_at: dt.datetime,
    sources: list[SourceRunSummary],
    lts_diff_path: Path | None = None,
    hin_match_report_path: Path | None = None,
) -> str:
    duration_s = (run_finished_at - run_started_at).total_seconds()
    lines = [
        "# Prep Report",
        "",
        f"- Run started: {run_started_at.isoformat()}",
        f"- Run finished: {run_finished_at.isoformat()}",
        f"- Duration: {duration_s:.0f} seconds",
        "",
        "## Per-source status",
        "",
        "| Source | Status | Records | Δ vs previous | Warnings |",
        "|---|---|---|---|---|",
    ]
    for s in sources:
        if s.previous_record_count is None:
            delta = "first run"
        else:
            d = s.record_count - s.previous_record_count
            delta = f"{'+' if d >= 0 else ''}{d}"
        warns = f"{len(s.warnings)} warning(s)" if s.warnings else "—"
        lines.append(f"| `{s.name}` | **{s.status}** | {s.record_count} | {delta} | {warns} |")
    lines.append("")

    has_warnings = any(s.warnings for s in sources)
    if has_warnings:
        lines.append("## Warnings detail")
        lines.append("")
        for s in sources:
            if not s.warnings:
                continue
            lines.append(f"### `{s.name}`")
            lines.append("")
            for w in s.warnings:
                lines.append(f"- {w}")
            lines.append("")

    lines.append("## Detail reports")
    lines.append("")
    if lts_diff_path is not None:
        lines.append(f"- LTS regression diff: `{lts_diff_path}`")
    if hin_match_report_path is not None:
        lines.append(f"- HIN match report: `{hin_match_report_path}`")
    lines.append("")

    failed = [s for s in sources if s.status == "FAIL"]
    if failed:
        lines.append("## ⚠ Build outcome")
        lines.append("")
        lines.append(
            f"**FAIL** — {len(failed)} source(s) failed; previous `bikemap.db` retained "
            "(all-or-nothing semantics, spec §3.9). Fix the failed source(s) and re-run."
        )
    elif any(s.status == "WARN" for s in sources):
        lines.append("## Build outcome")
        lines.append("")
        lines.append("**OK with warnings** — `bikemap.db` updated. Review warnings above.")
    else:
        lines.append("## Build outcome")
        lines.append("")
        lines.append("**OK** — `bikemap.db` updated cleanly.")

    return "\n".join(lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/prep/test_prep_report.py -v`
Expected: PASS (1 test)

- [ ] **Step 5: Commit**

```bash
git add chicago-bike-advocacy-map/prep/reporting/prep_report.py chicago-bike-advocacy-map/tests/prep/test_prep_report.py
git commit -m "feat(bikemap): add prep_report.md generator"
```

---

## Task 22: Main Pipeline Orchestrator (TDD)

**Files:**
- Create: `chicago-bike-advocacy-map/prep/main.py`
- Test: `chicago-bike-advocacy-map/tests/prep/test_main.py`

The orchestrator wires everything together: load config, run all fetchers + brokenspoke, run joins, build DB, emit reports. All-or-nothing semantics: any source fail → previous `bikemap.db` retained.

- [ ] **Step 1: Write the failing test**

```python
# tests/prep/test_main.py
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from prep.fetchers.base import FetchResult
from prep.main import PipelineResult, run_pipeline


def _write_yaml_config(path: Path) -> None:
    path.write_text(
        """
sources:
  hin:
    name: "Test HIN"
    type: "arcgis_feature_service"
    segments_url: "https://example.com/seg"
    intersections_url: "https://example.com/int"
    refresh_cadence: "monthly"
brokenspoke:
  image: "test/img:1.0"
  city_country: "united states"
  city_name: "chicago"
  city_state: "illinois"
  city_fips: "1714000"
  database_url: "postgresql://test"
  network_name: "test_net"
target:
  name: "Chicago"
  bbox:
    min_lat: 41.0
    max_lat: 42.0
    min_lng: -88.0
    max_lng: -87.0
"""
    )


@patch("prep.main.HinFetcher")
@patch("prep.main.CdotBikewaysFetcher")
@patch("prep.main.SpeedLimitsFetcher")
@patch("prep.main.CdpPoisFetcher")
@patch("prep.main.BrokenspokeRunner")
def test_run_pipeline_happy_path_writes_db_and_report(
    mock_bs: MagicMock,
    mock_cdp: MagicMock,
    mock_speed: MagicMock,
    mock_cdot: MagicMock,
    mock_hin: MagicMock,
    tmp_path: Path,
    fixtures_dir: Path,
) -> None:
    # Set up config
    cfg_path = tmp_path / "sources.yaml"
    _write_yaml_config(cfg_path)
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    db_path = tmp_path / "bikemap.db"
    treatments_dir = tmp_path / "treatments"
    treatments_dir.mkdir()
    results_dir = tmp_path / "brokenspoke_results" / "united-states" / "illinois" / "chicago" / "23.11"
    results_dir.mkdir(parents=True)

    # Each fetcher returns OK and writes plausible files into cache_dir/<date>/
    def _ok(records: int):
        return FetchResult(path=cache_dir, record_count=records, status="OK", warnings=[])

    mock_hin.return_value.fetch.return_value = _ok(50)
    mock_cdot.return_value.fetch.return_value = _ok(400)
    mock_speed.return_value.fetch.return_value = _ok(200)
    mock_cdp.return_value.fetch.return_value = _ok(60)

    # Brokenspoke runner returns the prepopulated results path
    mock_bs.return_value.run.return_value = results_dir
    # Populate it with sample geojson (2 segments + 2 intersections per fixture).
    import shutil
    shutil.copy(fixtures_dir / "neighborhood_ways_sample.geojson", results_dir / "neighborhood_ways.geojson")
    shutil.copy(fixtures_dir / "neighborhood_ways_intersections_sample.geojson", results_dir / "neighborhood_ways_intersections.geojson")
    # No HIN files placed in snapshot dir → orchestrator's _hin_features_from_geojson
    # returns ([], []), graceful empty-HIN path. Streets/intersections still get inserted
    # without HIN annotations.

    result = run_pipeline(
        config_path=cfg_path,
        cache_dir=cache_dir,
        brokenspoke_results_dir=tmp_path / "brokenspoke_results",
        db_path=db_path,
        treatments_dir=treatments_dir,
        report_path=tmp_path / "prep_report.md",
    )

    assert isinstance(result, PipelineResult)
    assert result.status == "OK"
    assert db_path.exists()
    assert (tmp_path / "prep_report.md").exists()

    # Verify the DB actually has the expected contents — not just that it exists.
    import sqlite3 as _sql
    conn = _sql.connect(db_path)
    try:
        streets_count = conn.execute("SELECT COUNT(*) FROM streets").fetchone()[0]
        ints_count = conn.execute("SELECT COUNT(*) FROM intersections").fetchone()[0]
        meta_rows = conn.execute("SELECT source FROM meta").fetchall()
        schema_meta = conn.execute("SELECT schema_version FROM schema_meta").fetchall()
    finally:
        conn.close()

    assert streets_count == 2, f"expected 2 streets from fixture, got {streets_count}"
    assert ints_count == 2, f"expected 2 intersections from fixture, got {ints_count}"
    # All four sources + brokenspoke recorded their meta.
    meta_sources = {row[0] for row in meta_rows}
    assert "hin" in meta_sources
    assert "cdot_bike_facilities" in meta_sources
    assert "chicago_speed_limits" in meta_sources
    assert "cdp_pois" in meta_sources
    assert "brokenspoke" in meta_sources
    assert len(schema_meta) == 1, "schema_meta must have exactly one row"


@patch("prep.main.HinFetcher")
def test_run_pipeline_failed_source_does_not_overwrite_existing_db(
    mock_hin: MagicMock,
    tmp_path: Path,
) -> None:
    cfg_path = tmp_path / "sources.yaml"
    _write_yaml_config(cfg_path)
    cache_dir = tmp_path / "cache"; cache_dir.mkdir()
    db_path = tmp_path / "bikemap.db"
    db_path.write_bytes(b"PREVIOUS_DB_CONTENTS")

    mock_hin.return_value.fetch.return_value = FetchResult(
        path=cache_dir, record_count=0, status="FAIL", warnings=["http 503"]
    )

    result = run_pipeline(
        config_path=cfg_path,
        cache_dir=cache_dir,
        brokenspoke_results_dir=tmp_path / "brokenspoke_results",
        db_path=db_path,
        treatments_dir=tmp_path / "treatments",
        report_path=tmp_path / "prep_report.md",
        skip_brokenspoke=True,
    )

    assert result.status == "FAIL"
    # Previous DB content unchanged
    assert db_path.read_bytes() == b"PREVIOUS_DB_CONTENTS"
```

Add `from unittest.mock import MagicMock` at the top of the test file.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/prep/test_main.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement the orchestrator**

```python
# prep/main.py
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path

from shapely import wkt
from shapely.geometry import shape

from prep.config_loader import load_sources_config
from prep.db.builder import DbBuilder
from prep.db.treatments_loader import load_treatments
from prep.fetchers.base import today_snapshot_dir, rotate_snapshots
from prep.fetchers.cdot_sanity import CdotBikewaysFetcher
from prep.fetchers.hin import HinFetcher
from prep.fetchers.pois_cdp import CdpPoisFetcher
from prep.fetchers.speed_limits import SpeedLimitsFetcher
from prep.joins.graph_link import compute_head_tail_nodes
from prep.joins.hin_to_osm import (
    HinIntersectionFeature,
    HinIntersectionMatch,
    HinSegmentFeature,
    HinSegmentMatch,
    OsmIntersection,
    OsmSegment,
    join_hin_intersections_to_osm,
    join_hin_segments_to_osm,
)
from prep.lts.ingest import (
    ingest_brokenspoke_pois,
    ingest_intersections,
    ingest_segments,
)
from prep.lts.runner import BrokenspokeRunner
from prep.reporting.hin_match_report import build_hin_match_report
from prep.reporting.lts_diff import diff_lts_against_previous
from prep.reporting.prep_report import SourceRunSummary, build_prep_report

CODE_VERSION = "0.1.0"


def _accumulate_segment_matches(
    matches: list[HinSegmentMatch],
) -> dict[int, HinSegmentMatch]:
    """Reduce 1:N HIN→OSM segment matches to one per OSM segment.

    Modal flags are OR'd across matches (any match where bike=true → bike=true).
    Severity rank takes the max (worst severity wins). Feature IDs are
    deliberately not tracked here — `hin_match_report` consumes the raw match
    list. This returns the per-OSM annotation only.
    """
    out: dict[int, HinSegmentMatch] = {}
    for m in matches:
        existing = out.get(m.osm_id)
        if existing is None:
            out[m.osm_id] = m
            continue
        merged_flags = {
            "bike": existing.modal_flags.get("bike", False) or m.modal_flags.get("bike", False),
            "ped": existing.modal_flags.get("ped", False) or m.modal_flags.get("ped", False),
        }
        # Use max for severity if both present.
        if existing.severity_rank is None:
            sev = m.severity_rank
        elif m.severity_rank is None:
            sev = existing.severity_rank
        else:
            sev = max(existing.severity_rank, m.severity_rank)
        out[m.osm_id] = HinSegmentMatch(
            osm_id=m.osm_id,
            hin_feature_id=existing.hin_feature_id,  # keep first id (display only)
            modal_flags=merged_flags,
            severity_rank=sev,
        )
    return out


def _accumulate_intersection_matches(
    matches: list[HinIntersectionMatch],
) -> dict[int, HinIntersectionMatch]:
    """Same reducer as _accumulate_segment_matches, for intersection matches."""
    out: dict[int, HinIntersectionMatch] = {}
    for m in matches:
        existing = out.get(m.osm_id)
        if existing is None:
            out[m.osm_id] = m
            continue
        merged_flags = {
            "bike": existing.modal_flags.get("bike", False) or m.modal_flags.get("bike", False),
            "ped": existing.modal_flags.get("ped", False) or m.modal_flags.get("ped", False),
        }
        if existing.severity_rank is None:
            sev = m.severity_rank
        elif m.severity_rank is None:
            sev = existing.severity_rank
        else:
            sev = max(existing.severity_rank, m.severity_rank)
        out[m.osm_id] = HinIntersectionMatch(
            osm_id=m.osm_id,
            hin_feature_id=existing.hin_feature_id,
            modal_flags=merged_flags,
            severity_rank=sev,
        )
    return out


@dataclass(frozen=True)
class PipelineResult:
    status: str  # "OK" | "WARN" | "FAIL"
    sources: list[SourceRunSummary]


def _hin_features_from_geojson(path: Path, kind: str) -> tuple[list, list]:
    """Parse hin_segments.geojson or hin_intersections.geojson into typed features."""
    if not path.exists():
        return [], []
    data = json.loads(path.read_text())
    segs: list[HinSegmentFeature] = []
    ints: list[HinIntersectionFeature] = []
    for feat in data.get("features", []):
        props = feat.get("properties", {})
        geom = shape(feat["geometry"])
        modal = {
            "bike": bool(props.get("MODE_BIKE")),
            "ped": bool(props.get("MODE_PED")),
        }
        sev = props.get("SEVERITY_RANK")
        fid = str(props.get("OBJECTID") or props.get("GlobalID") or props.get("id"))
        if kind == "segment":
            segs.append(HinSegmentFeature(
                feature_id=fid, geometry=geom, modal_flags=modal, severity_rank=sev,
            ))
        else:
            ints.append(HinIntersectionFeature(
                feature_id=fid, geometry=geom, modal_flags=modal, severity_rank=sev,
            ))
    return segs, ints


def run_pipeline(
    *,
    config_path: Path,
    cache_dir: Path,
    brokenspoke_results_dir: Path,
    db_path: Path,
    treatments_dir: Path,
    report_path: Path,
    skip_brokenspoke: bool = False,
) -> PipelineResult:
    """Run the full prep pipeline. All-or-nothing: failures preserve previous DB."""
    started = dt.datetime.now(dt.timezone.utc)
    cfg = load_sources_config(config_path)
    snapshot_dir = today_snapshot_dir(cache_dir)

    sources: list[SourceRunSummary] = []

    # 1. Run all fetchers
    hin_src = cfg.sources.get("hin")
    if hin_src is not None:
        hin = HinFetcher(
            segments_url=hin_src.extra["segments_url"],
            intersections_url=hin_src.extra["intersections_url"],
        )
        r = hin.fetch(snapshot_dir)
        sources.append(SourceRunSummary(
            name="hin", status=r.status, record_count=r.record_count,
            previous_record_count=None, warnings=r.warnings,
        ))

    cdot_src = cfg.sources.get("cdot_bike_facilities")
    if cdot_src is not None:
        cdot = CdotBikewaysFetcher(
            domain=cdot_src.extra["domain"],
            dataset_id=cdot_src.extra["dataset_id"],
        )
        r = cdot.fetch(snapshot_dir)
        sources.append(SourceRunSummary(
            name="cdot_bike_facilities", status=r.status, record_count=r.record_count,
            previous_record_count=None, warnings=r.warnings,
        ))

    speed_src = cfg.sources.get("chicago_speed_limits")
    if speed_src is not None:
        speed = SpeedLimitsFetcher(
            domain=speed_src.extra["domain"],
            dataset_id=speed_src.extra["dataset_id"],
        )
        r = speed.fetch(snapshot_dir)
        sources.append(SourceRunSummary(
            name="chicago_speed_limits", status=r.status, record_count=r.record_count,
            previous_record_count=None, warnings=r.warnings,
        ))

    aldr_src = cfg.sources.get("cdp_alderman_offices")
    lib_src = cfg.sources.get("cdp_library_branches")
    if aldr_src is not None and lib_src is not None:
        cdp = CdpPoisFetcher(
            domain=aldr_src.extra["domain"],
            alderman_dataset_id=aldr_src.extra["dataset_id"],
            library_dataset_id=lib_src.extra["dataset_id"],
        )
        r = cdp.fetch(snapshot_dir)
        sources.append(SourceRunSummary(
            name="cdp_pois", status=r.status, record_count=r.record_count,
            previous_record_count=None, warnings=r.warnings,
        ))

    # 2. Run brokenspoke (unless skipped)
    results_path: Path | None = None
    if not skip_brokenspoke:
        try:
            runner = BrokenspokeRunner(
                image=cfg.brokenspoke.image,
                city_country=cfg.brokenspoke.city_country,
                city_name=cfg.brokenspoke.city_name,
                city_state=cfg.brokenspoke.city_state,
                city_fips=cfg.brokenspoke.city_fips,
                database_url=cfg.brokenspoke.database_url,
                network_name=cfg.brokenspoke.network_name,
                results_dir=brokenspoke_results_dir,
                compose_file=Path(cfg.brokenspoke.compose_file),
            )
            results_path = runner.run()
            sources.append(SourceRunSummary(
                name="brokenspoke", status="OK",
                record_count=0,  # filled in after ingest
                previous_record_count=None, warnings=[],
            ))
        except Exception as e:  # noqa: BLE001
            sources.append(SourceRunSummary(
                name="brokenspoke", status="FAIL",
                record_count=0,
                previous_record_count=None, warnings=[f"brokenspoke run failed: {e}"],
            ))

    # 3. Read previous DB's meta (for delta calculation) — done before FAIL check
    # so FAIL reports also show meaningful "Δ vs previous" data.
    previous_record_counts: dict[str, int] = {}
    if db_path.exists():
        try:
            prev_conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            try:
                rows = prev_conn.execute(
                    "SELECT source, record_count FROM meta"
                ).fetchall()
                previous_record_counts = {src: cnt for src, cnt in rows}
            finally:
                prev_conn.close()
        except sqlite3.Error:
            # Previous DB malformed — ignore, treat as no-previous.
            previous_record_counts = {}

    # Backfill previous_record_count on all source summaries.
    sources = [
        SourceRunSummary(
            name=s.name,
            status=s.status,
            record_count=s.record_count,
            previous_record_count=previous_record_counts.get(s.name),
            warnings=list(s.warnings),
        )
        for s in sources
    ]

    # 4. All-or-nothing check
    if any(s.status == "FAIL" for s in sources):
        finished = dt.datetime.now(dt.timezone.utc)
        report = build_prep_report(
            run_started_at=started, run_finished_at=finished, sources=sources,
        )
        report_path.write_text(report)
        return PipelineResult(status="FAIL", sources=sources)

    # 5. Build new DB to a temp path; swap atomically only on success
    tmp_fd, tmp_name = tempfile.mkstemp(suffix=".db", dir=db_path.parent)
    os.close(tmp_fd)
    tmp_db = Path(tmp_name)
    try:
        builder = DbBuilder(tmp_db)
        builder.create_schema()

        if results_path is not None:
            segs = list(ingest_segments(results_path))
            ints = list(ingest_intersections(results_path))
            pois = list(ingest_brokenspoke_pois(results_path))

            # 6. Spatial-join HIN onto OSM
            hin_seg_path = snapshot_dir / "hin_segments.geojson"
            hin_int_path = snapshot_dir / "hin_intersections.geojson"
            hin_segs, _ = _hin_features_from_geojson(hin_seg_path, "segment")
            _, hin_ints = _hin_features_from_geojson(hin_int_path, "intersection")

            # Convert SegmentRecord/IntersectionRecord to OsmSegment/OsmIntersection
            # using shapely's standard WKT parser. NEVER hand-roll WKT parsing.
            osm_segs = [
                OsmSegment(osm_id=s.osm_id, geometry=wkt.loads(s.geometry_wkt))
                for s in segs
            ]
            osm_ints = [
                OsmIntersection(osm_id=i.osm_id, geometry=wkt.loads(i.geometry_wkt))
                for i in ints
            ]

            seg_matches = list(join_hin_segments_to_osm(
                hin_segments=hin_segs, osm_segments=osm_segs,
            ))
            int_matches = list(join_hin_intersections_to_osm(
                hin_intersections=hin_ints, osm_intersections=osm_ints,
            ))

            # Accumulate 1:N matches: same OSM feature may match multiple HIN
            # features. OR the modal flags, take MAX of severity ranks.
            seg_match_map = _accumulate_segment_matches(seg_matches)
            int_match_map = _accumulate_intersection_matches(int_matches)

            # 5.5. Compute head/tail node IDs (Task 16a). If brokenspoke emits
            # these directly on segments (verified during Task 25), prefer them;
            # otherwise compute from segment endpoints + intersection points.
            head_tail_map = compute_head_tail_nodes(
                segments=[(s.osm_id, wkt.loads(s.geometry_wkt)) for s in segs],
                intersections=[(i.osm_id, wkt.loads(i.geometry_wkt)) for i in ints],
            )

            builder.insert_streets(
                segs,
                hin_matches=seg_match_map,
                head_tail=head_tail_map,
            )
            builder.insert_intersections(ints, hin_matches=int_match_map)
            builder.insert_pois(pois)

            # HIN match report
            hin_report = build_hin_match_report(
                hin_segments=hin_segs,
                hin_intersections=hin_ints,
                segment_matches=seg_matches,
                intersection_matches=int_matches,
            )
            (report_path.parent / "hin_match_report.md").write_text(hin_report.to_markdown())

        # 6. Treatments
        load_treatments(treatments_dir, builder)

        # 7. Source meta
        for s in sources:
            builder.record_meta(s.name, s.record_count, s.status)
        builder.record_schema_meta(code_version=CODE_VERSION)
        builder.close()

        # 8. LTS regression diff (vs previous bikemap.db, if any)
        if db_path.exists():
            diff = diff_lts_against_previous(current_db=tmp_db, previous_db=db_path)
            (report_path.parent / "lts_diff.md").write_text(diff.to_markdown())

        # 9. Atomic swap
        shutil.move(str(tmp_db), str(db_path))

    except Exception as e:  # noqa: BLE001
        if tmp_db.exists():
            tmp_db.unlink()
        sources.append(SourceRunSummary(
            name="db_build", status="FAIL",
            record_count=0, previous_record_count=None,
            warnings=[f"build failed: {e}"],
        ))

    finished = dt.datetime.now(dt.timezone.utc)
    rotate_snapshots(cache_dir, keep=3)

    overall = "OK"
    if any(s.status == "FAIL" for s in sources):
        overall = "FAIL"
    elif any(s.status == "WARN" for s in sources):
        overall = "WARN"

    report = build_prep_report(
        run_started_at=started,
        run_finished_at=finished,
        sources=sources,
        lts_diff_path=report_path.parent / "lts_diff.md",
        hin_match_report_path=report_path.parent / "hin_match_report.md",
    )
    report_path.write_text(report)

    return PipelineResult(status=overall, sources=sources)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the bikemap prep pipeline.")
    parser.add_argument(
        "--config", type=Path, default=Path("prep/config/sources.yaml"),
    )
    parser.add_argument("--cache-dir", type=Path, default=Path("data/cache"))
    parser.add_argument(
        "--brokenspoke-results-dir", type=Path, default=Path("data/brokenspoke_results"),
    )
    parser.add_argument("--db", type=Path, default=Path("data/bikemap.db"))
    parser.add_argument("--treatments-dir", type=Path, default=Path("treatments"))
    parser.add_argument("--report", type=Path, default=Path("prep_report.md"))
    parser.add_argument(
        "--skip-brokenspoke", action="store_true",
        help="Skip the brokenspoke run (useful when iterating on ingest/join code).",
    )
    args = parser.parse_args()

    args.cache_dir.mkdir(parents=True, exist_ok=True)
    args.brokenspoke_results_dir.mkdir(parents=True, exist_ok=True)
    args.db.parent.mkdir(parents=True, exist_ok=True)

    result = run_pipeline(
        config_path=args.config,
        cache_dir=args.cache_dir,
        brokenspoke_results_dir=args.brokenspoke_results_dir,
        db_path=args.db,
        treatments_dir=args.treatments_dir,
        report_path=args.report,
        skip_brokenspoke=args.skip_brokenspoke,
    )

    return 0 if result.status != "FAIL" else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/prep/test_main.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Run full test suite to ensure nothing is broken**

Run: `pytest -v`
Expected: ALL PASS (test count grows as tasks land — should be ~30+ tests passing).

- [ ] **Step 6: Commit**

```bash
git add chicago-bike-advocacy-map/prep/main.py chicago-bike-advocacy-map/tests/prep/test_main.py
git commit -m "feat(bikemap): add main pipeline orchestrator with all-or-nothing semantics"
```

---

## Task 23: Makefile

**Files:**
- Create: `chicago-bike-advocacy-map/Makefile`

- [ ] **Step 1: Write the Makefile**

```makefile
# Makefile for chicago-bike-advocacy-map prep pipeline.
.PHONY: help dev test lint type refresh refresh-skip-brokenspoke report clean

# Auto-detect venv: prefer .venv/, fall back to venv/, fall back to system python.
PY := $(shell if [ -x .venv/bin/python ]; then echo .venv/bin/python; \
              elif [ -x venv/bin/python ]; then echo venv/bin/python; \
              else echo python; fi)
PIP := $(shell if [ -x .venv/bin/pip ]; then echo .venv/bin/pip; \
               elif [ -x venv/bin/pip ]; then echo venv/bin/pip; \
               else echo pip; fi)

help:
	@echo "Targets:"
	@echo "  test                     - run pytest with coverage"
	@echo "  lint                     - run ruff"
	@echo "  type                     - run mypy"
	@echo "  refresh                  - run full prep pipeline (~30-90 min for Chicago)"
	@echo "  refresh-skip-brokenspoke - run prep without brokenspoke (fast, for iteration)"
	@echo "  report                   - open prep_report.md"
	@echo "  clean                    - remove caches, results, prep_report.md"

test:
	$(PY) -m pytest --cov=prep --cov-report=term-missing

lint:
	$(PY) -m ruff check .

type:
	$(PY) -m mypy prep

refresh:
	$(PY) -m prep.main \
		--config prep/config/sources.yaml \
		--cache-dir data/cache \
		--brokenspoke-results-dir data/brokenspoke_results \
		--db data/bikemap.db \
		--treatments-dir treatments \
		--report prep_report.md

refresh-skip-brokenspoke:
	$(PY) -m prep.main \
		--config prep/config/sources.yaml \
		--cache-dir data/cache \
		--brokenspoke-results-dir data/brokenspoke_results \
		--db data/bikemap.db \
		--treatments-dir treatments \
		--report prep_report.md \
		--skip-brokenspoke

report:
	@if [ -f prep_report.md ]; then \
		open prep_report.md 2>/dev/null || cat prep_report.md; \
	else \
		echo "No prep_report.md yet. Run 'make refresh' first."; \
	fi

clean:
	rm -rf data/cache/* data/brokenspoke_results/*
	rm -f prep_report.md hin_match_report.md lts_diff.md
```

- [ ] **Step 2: Verify make targets are syntactically valid**

```bash
cd chicago-bike-advocacy-map
make help
```

Expected: prints the help block with no errors.

- [ ] **Step 3: Commit**

```bash
git add chicago-bike-advocacy-map/Makefile
git commit -m "feat(bikemap): add Makefile entry points"
```

---

## Task 24: GitHub Actions CI

**Files:**
- Create: `chicago-bike-advocacy-map/.github/workflows/ci.yml`

Per spec §3.11: `ruff` + `mypy` + `pytest` on push.

- [ ] **Step 1: Write the workflow**

```yaml
# .github/workflows/ci.yml
name: ci

on:
  push:
    branches: [main]
    paths: ["chicago-bike-advocacy-map/**", ".github/workflows/ci.yml"]
  pull_request:
    paths: ["chicago-bike-advocacy-map/**", ".github/workflows/ci.yml"]

jobs:
  build:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: chicago-bike-advocacy-map
    steps:
      - uses: actions/checkout@v4

      - name: Install system deps for geopandas
        run: |
          sudo apt-get update
          sudo apt-get install -y libgdal-dev gdal-bin libgeos-dev libproj-dev libspatialite-dev

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt

      - name: Lint
        run: ruff check .

      - name: Type-check
        run: mypy prep

      - name: Test
        run: pytest --cov=prep --cov-report=xml --cov-fail-under=60
```

**Why 60% (not the 80% in spec §3.11):** v1 baseline is realistic. Orchestrator integration paths and Docker-shelling subprocess wrappers are hard to fully cover with mocks. A 60% floor catches "I forgot to write the test" without red-blocking every push during the build-out. Raise the floor to 80% in a follow-up commit once the codebase stabilizes after the smoke run.

- [ ] **Step 2: Commit**

```bash
git add chicago-bike-advocacy-map/.github/workflows/ci.yml
git commit -m "ci(bikemap): add GitHub Actions workflow (ruff + mypy + pytest)"
```

---

## Task 25: Smoke Run on a Small City (Verify brokenspoke End-to-End)

This task is a **manual integration test**, not a code task. The goal is to verify the brokenspoke runner + ingest + DB build pipeline works end-to-end on a small city before committing to the much-larger Chicago run. Use Santa Rosa, NM (FIPS 3570670, ~3000 people) — what the brokenspoke README itself uses for examples.

**Prerequisites:** Docker Desktop running. ~10 GB free disk.

- [ ] **Step 1: Modify config to point at Santa Rosa for the smoke run**

Create `chicago-bike-advocacy-map/prep/config/sources_smoke.yaml`:

```yaml
sources:
  # No HTTP fetchers in smoke (we don't need HIN/CDOT for Santa Rosa).
brokenspoke:
  image: "ghcr.io/peopleforbikes/brokenspoke-analyzer:3.1.1"
  city_country: "united states"
  city_name: "santa rosa"
  city_state: "new mexico"
  city_fips: "3570670"
  database_url: "postgresql://postgres:postgres@postgres:5432/postgres"
  network_name: "brokenspoke-analyzer_default"
target:
  name: "Santa Rosa NM"
  bbox:
    min_lat: 34.92
    max_lat: 34.96
    min_lng: -104.70
    max_lng: -104.66
```

- [ ] **Step 2: Pull the brokenspoke Docker image first to surface any auth/network issues**

```bash
docker pull ghcr.io/peopleforbikes/brokenspoke-analyzer:3.1.1
```

Expected: image pulls cleanly. If "denied" — check Docker login / auth.

- [ ] **Step 3: Run the smoke pipeline**

```bash
cd chicago-bike-advocacy-map
.venv/bin/python -m prep.main \
  --config prep/config/sources_smoke.yaml \
  --cache-dir data/cache \
  --brokenspoke-results-dir data/brokenspoke_results \
  --db data/bikemap_smoke.db \
  --treatments-dir treatments \
  --report prep_report_smoke.md
```

Expected runtime: 5-15 minutes for Santa Rosa.

- [ ] **Step 4: Inspect the actual brokenspoke output column names**

```bash
ls data/brokenspoke_results/united-states/new-mexico/santa-rosa/
# pick the version directory, e.g., 23.11/
.venv/bin/python -c "
import json
from pathlib import Path
p = Path('data/brokenspoke_results/united-states/new-mexico/santa-rosa').iterdir().__next__()
ways = json.loads((p / 'neighborhood_ways.geojson').read_text())
print('SEGMENT property keys:', sorted(ways['features'][0]['properties'].keys()))
ints = json.loads((p / 'neighborhood_ways_intersections.geojson').read_text())
print('INTERSECTION property keys:', sorted(ints['features'][0]['properties'].keys()))
"
```

Capture the output. Compare to the `SEG_*` and `INT_*` constants in `prep/lts/ingest.py`.

- [ ] **Step 5: If field names differ, update `prep/lts/ingest.py`**

If brokenspoke uses different property keys than our placeholders (`osm_id`, `ft_lts`, `tf_lts`, `lts_approach`, etc.), update the constants in `prep/lts/ingest.py` to match real values. Re-run unit tests:

```bash
pytest tests/prep/test_lts_ingest.py -v
```

If the tests fail because the fixtures use the old names, update fixtures too (in `tests/fixtures/neighborhood_ways_sample.geojson` and `neighborhood_ways_intersections_sample.geojson`).

- [ ] **Step 6: Verify `bikemap_smoke.db` has expected tables and rows**

```bash
sqlite3 data/bikemap_smoke.db <<'EOF'
.tables
SELECT COUNT(*) AS streets FROM streets;
SELECT COUNT(*) AS intersections FROM intersections;
SELECT COUNT(*) AS pois FROM pois;
SELECT COUNT(*) AS treatments FROM treatments;
SELECT category, COUNT(*) FROM pois GROUP BY category;
SELECT lts, COUNT(*) FROM streets GROUP BY lts ORDER BY lts;
EOF
```

Expected:
- `streets` count > 0 (Santa Rosa has ~hundreds of streets)
- `intersections` count > 0
- `pois` count > 0 (brokenspoke emits some POIs even for tiny towns)
- `treatments` count == 5
- LTS distribution looks plausible (not all LTS 3 or all LTS 1)

- [ ] **Step 7: Open `prep_report_smoke.md` and confirm formatting**

```bash
cat prep_report_smoke.md
```

Expected: per-source rows, OK/WARN/FAIL badges, build outcome at bottom.

- [ ] **Step 8: Commit any ingest.py / fixture updates**

If you made changes in Step 5:

```bash
git add chicago-bike-advocacy-map/prep/lts/ingest.py chicago-bike-advocacy-map/tests/fixtures/
git commit -m "fix(bikemap): align ingest field names with actual brokenspoke output"
```

If no changes were needed, skip this step.

- [ ] **Step 9: Document smoke findings**

Create `chicago-bike-advocacy-map/docs/smoke-findings.md` with the verified information from Steps 4 and 6:

```markdown
# Smoke Run Findings (Santa Rosa, NM)

Date: <run date>
Brokenspoke version: 3.1.1

## Verified field names

### neighborhood_ways.geojson
- `osm_id`: ...
- LTS field(s): ... (single `lts`? `ft_lts`/`tf_lts`?)
- Other relevant fields: ...

### neighborhood_ways_intersections.geojson
- `osm_id`: ...
- LTS field: ...

### POI exports
- Files emitted: <list>
- Common fields: name, category, ...

## Counts (Santa Rosa NM, ~3000 pop)

- streets: <N>
- intersections: <N>
- pois: <N>
- LTS distribution: LTS 1: X%, LTS 2: Y%, LTS 3: Z%

## Runtime

Total wall-clock: <minutes>
Disk peak: <GB>
```

Commit:

```bash
git add chicago-bike-advocacy-map/docs/smoke-findings.md
git commit -m "docs(bikemap): record smoke run findings"
```

---

## Task 26: Real Chicago Run + Capture Findings for Plan 2

The full Chicago run. Expect 30-90 min runtime + 5-15 GB temporary disk during PostgreSQL+OSM extract.

**Prerequisites:** Smoke run (Task 25) succeeded. Docker Desktop has ≥ 4 CPUs and ≥ 8 GB RAM allocated (per brokenspoke docs).

- [ ] **Step 1: Verify CMAP HIN endpoint URLs in `sources.yaml`**

The placeholder URLs in `prep/config/sources.yaml` may not match the actual 2025 SAP HIN endpoint. Verify:

1. Visit https://hub-cookcountyil.opendata.arcgis.com/
2. Search for "Safety Action Plan" or "High Injury Network."
3. Find the segment and intersection feature layers; copy their REST endpoint URLs.
4. Update `sources.yaml` with the real URLs.

If the layers aren't yet published as separate Feature Services, fall back to the legacy 2015 HIN service for v1:

```yaml
hin:
  segments_url: "https://services1.arcgis.com/tp9wqSVX1AitKgjd/arcgis/rest/services/hin_082015/FeatureServer/0"
  # 2015 service may not have a separate intersections layer — set to empty endpoint
  # to skip intersection HIN annotations until 2025 SAP layers land.
  intersections_url: ""
```

If the intersections URL is empty, the HIN fetcher will return zero intersections (acceptable degradation).

Commit any URL updates:

```bash
git add chicago-bike-advocacy-map/prep/config/sources.yaml
git commit -m "fix(bikemap): pin verified CMAP HIN endpoint URLs"
```

- [ ] **Step 2: Run the full pipeline**

```bash
cd chicago-bike-advocacy-map
make refresh
```

Expected runtime: 30-90 minutes. Watch logs; if brokenspoke fails midway, re-run after `docker compose down && docker volume rm brokenspoke-analyzer_postgres`.

- [ ] **Step 3: Inspect `prep_report.md`**

```bash
cat prep_report.md
```

Expected:
- All sources OK (or WARN with documented warnings).
- HIN: hundreds to thousands of segments + intersections.
- brokenspoke: tens of thousands of streets and intersections for Chicago.
- LTS regression diff: empty (first run; no prior DB).

- [ ] **Step 4: Inspect `hin_match_report.md`**

Per launch criterion §6.4 #2, overall match rate must be ≥ 95%. If not:

- Investigate cause: are the buffer/bearing tolerances too tight? Are the HIN feature IDs unique? Are there geometry projection issues?
- Tune `SEG_BUFFER_METERS` or `SEG_BEARING_TOLERANCE_DEG` in `prep/joins/hin_to_osm.py` and re-run prep.
- Document final tuned values in `docs/smoke-findings.md`.

- [ ] **Step 5: Verify bikemap.db row counts**

```bash
sqlite3 data/bikemap.db <<'EOF'
SELECT
  (SELECT COUNT(*) FROM streets) AS streets,
  (SELECT COUNT(*) FROM intersections) AS intersections,
  (SELECT COUNT(*) FROM pois) AS pois,
  (SELECT COUNT(*) FROM hin_features) AS hin_features,
  (SELECT COUNT(*) FROM treatments) AS treatments;

SELECT category, COUNT(*) FROM pois GROUP BY category ORDER BY 2 DESC;
SELECT lts, COUNT(*) FROM streets GROUP BY lts ORDER BY lts;
EOF
```

Expected for Chicago:
- streets: 30,000–100,000
- intersections: 20,000–50,000
- pois: 1,000s
- LTS distribution: substantial counts in each tier (LTS 3 typically 10-30%)

If LTS distribution is lopsided (e.g., 95% LTS 1 or 95% LTS 3), brokenspoke may have produced bad output — investigate before proceeding to Plan 2.

- [ ] **Step 6: Hand-validate LTS for known Chicago streets**

Pick 5 known streets and verify LTS makes sense:

```bash
sqlite3 data/bikemap.db <<'EOF'
.headers on
SELECT name, lts, highway, speed FROM streets
WHERE name LIKE '%Milwaukee%' OR name LIKE '%Western%' OR name LIKE '%Lincoln%'
   OR name LIKE '%Lake Shore%' OR name LIKE '%lakefront%'
LIMIT 30;
EOF
```

Sanity check:
- Western Ave should be predominantly LTS 3 (high-speed multi-lane arterial).
- Lakefront Trail should be LTS 1.
- Milwaukee Ave: mixed (LTS 2-3 depending on whether the segment has the protected lane).
- Residential side streets: LTS 1-2.

Document findings in `docs/smoke-findings.md`. If LTS doesn't roughly match expectations, do NOT proceed to Plan 2 — investigate first.

- [ ] **Step 7: Capture POI quality findings**

```bash
sqlite3 data/bikemap.db <<'EOF'
.headers on
SELECT category, name, source FROM pois WHERE category = 'school' LIMIT 20;
SELECT category, name, source FROM pois WHERE category = 'park' LIMIT 20;
SELECT category, name, source FROM pois WHERE category = 'grocery' LIMIT 20;
SELECT category, name, source FROM pois WHERE category = 'hospital' LIMIT 20;
SELECT category, name, source FROM pois WHERE category = 'alderman' LIMIT 5;
SELECT category, name, source FROM pois WHERE category = 'library' LIMIT 5;
EOF
```

For each category, evaluate whether brokenspoke's coverage is adequate. Document in `docs/smoke-findings.md` whether to keep brokenspoke as primary or fall back to CDP for any category. (Spec §3.3 v1-time evaluation.)

- [ ] **Step 8: Update `docs/smoke-findings.md` with Chicago results**

Append to the file from Task 25 a "Chicago full-run findings" section with:
- Final field name confirmations
- Row counts
- LTS distribution
- POI category coverage decisions
- HIN match rate
- Final tuned spatial-join tolerances
- Total runtime
- Any quirks observed

Commit:

```bash
git add chicago-bike-advocacy-map/docs/smoke-findings.md
git commit -m "docs(bikemap): record full Chicago prep run findings"
```

- [ ] **Step 9: Push to the GitHub repo (subtree push from the monorepo)**

The local working directory is a Claude monorepo (sibling to `chicago-pipeline/`, `notion-database/`, etc.). The GitHub repo is `ZombieHunter386/Lakeview-Bike-Grid` (currently empty). We push only the `chicago-bike-advocacy-map/` subdirectory, not the entire monorepo.

**One-time setup** (only needed before the first push):

```bash
# Add the bike-map repo as a separate remote (anchored to the subdirectory).
cd /Users/hunterheyman/Claude/.claude/worktrees/affectionate-hawking-e216bd
git remote add bikemap https://github.com/ZombieHunter386/Lakeview-Bike-Grid.git

# Optional: rename the GitHub repo to chicago-bike-advocacy-map via gh CLI:
# gh repo rename chicago-bike-advocacy-map --repo ZombieHunter386/Lakeview-Bike-Grid
# (Update the `bikemap` remote URL afterward.)
```

**Each push** uses `git subtree push` to extract only the bike-map subdir:

```bash
git subtree push --prefix=chicago-bike-advocacy-map bikemap main
```

This rewrites only `chicago-bike-advocacy-map/`'s history into the bikemap remote's `main` branch — none of the other monorepo siblings (chicago-pipeline, notion-database, etc.) leak.

If subtree push fails because the bikemap repo's history has diverged (e.g., a manual commit on the GitHub side), use:

```bash
git subtree split --prefix=chicago-bike-advocacy-map -b _bikemap_split
git push bikemap _bikemap_split:main
git branch -D _bikemap_split
```

Verify on GitHub that the `chicago-bike-advocacy-map/` directory's contents are at the repo root (not nested), `data/bikemap.db` is gitignored (not pushed), and the README displays correctly.

- [ ] **Step 10: Plan 2 readiness checklist**

Plan 1 is complete and Plan 2 (web service) is unblocked when:

- [ ] `data/bikemap.db` exists, ~150-250 MB, all tables populated
- [ ] `prep_report.md` shows all sources OK or WARN (no FAIL)
- [ ] `hin_match_report.md` shows ≥ 95% overall match rate
- [ ] LTS distribution on known Chicago streets is plausible
- [ ] `docs/smoke-findings.md` documents the verified field names and any tuned parameters
- [ ] CI is green
- [ ] All tests pass: `make test` exits 0

If any of these aren't met, fix before starting Plan 2.

---

## Self-Review Notes

**Spec coverage check:**

- §0.1 canonical tier definitions → encoded in `routing_weights.yaml` (Task 4); consumed by Plan 2.
- §1 product framing → context, not implementation.
- §2 interaction model → Plan 3 territory.
- §3.1-3.4 data architecture → Tasks 3, 5-13 (fetchers + brokenspoke + 10a postgres compose).
- §3.5 gap cache → schema declared in Task 15 (`cache_schema.sql`); writes happen in Plan 2.
- §3.6 POI selection rules → encoded in pois table + Plan 2's `app.core.poi_picker`.
- §3.7 map services → frontend; Plan 3.
- §3.8 privacy → web service; Plan 2.
- §3.9 refresh → Tasks 22 (orchestrator) + 23 (Makefile) + 21 (prep_report).
- §3.10 web service ops → Plan 2 (includes `head_node_osm_id`/`tail_node_osm_id` graph build, populated by Task 16a in this plan).
- §3.11 CI/CD → Task 24.
- §3.12 HIN-to-OSM join → Task 14 (RTree-indexed for Chicago scale).
- §3.13 testing strategy → Tasks 19 (LTS diff) + 20 (HIN match) + ongoing pytest.
- §3.14 non-goals → respected throughout (no mobile, no auth, etc.).
- §4 routing model → Plan 2 (the routing engine itself; routing_weights.yaml from Task 4 is its authoritative input).
- §5 system architecture → Tasks 1, 22-24.
- §6 V1 scope → Tasks 25-26 verify launch criteria for the data layer specifically.
- §7 open research items → Task 26 resolves §7.1 #1, #2, #2a, #4.

Items deferred to Plan 2 (web service): all of §3.10, §4 routing, §5 web app modules, §6 web service launch criteria.

Items deferred to Plan 3 (frontend): §2 entirely, §3.7, §3.8 client-side bits.

**Placeholder check:** No "TBD"/"TODO"/"implement later" — every task has actual code.

**Type-consistency check:**
- `SegmentRecord` defined in Task 12, consumed in Tasks 13/16/22 — same fields throughout.
- `IntersectionRecord` defined in Task 12, consumed in 16/22.
- `PoiRecord` defined in Task 13, consumed in 16/22.
- `HinSegmentMatch`/`HinIntersectionMatch` defined in Task 14, consumed in 16/22.
- `FetchResult` defined in Task 6, consumed in 7-10/22.
- `BrokenspokeConfig` defined in Task 3, extended with `compose_file` field in Task 10a, consumed in Task 11/22.
- `compute_head_tail_nodes` defined in Task 16a, consumed in Task 22.

---

## Plan Complete

Plan saved to `docs/superpowers/plans/2026-05-05-chicago-bike-map-01-prep-pipeline.md`.

Total tasks: **27** (18 TDD code tasks + 9 ops/integration tasks; Tasks 10a + 16a added during plan revision). Estimated effort: **7-13 development days** for the code tasks, plus **2 days** of compute + investigation for the smoke + Chicago runs.

**Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?

