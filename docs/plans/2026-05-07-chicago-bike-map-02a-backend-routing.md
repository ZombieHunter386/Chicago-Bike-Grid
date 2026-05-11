# Plan 2A — Backend + Routing Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Flask web service that loads `bikemap.db` into an in-memory routing graph at startup and serves `/routes`, `/gap-analysis`, `/pois`, `/treatments`, `/geocode`, and `/health` endpoints — meeting spec §3.1 (web service column), §3.10 (web service ops), §4 (routing), §3.5 (gap caching).

**Architecture:** Flask app loads `bikemap.db` (read-only) into an `igraph` directed graph at startup; per-tier base edge weights and a scipy KD-tree of intersection coordinates are precomputed once. Route requests run Dijkstra over the shared graph; gap analysis mutates a per-call edge-weights vector (never the graph itself) to keep memory bounded. A separate writable `cache.db` stores hashed gap results, evicted by LRU when it crosses 500 MB. Async gap-analysis polling (job_id pattern) frees workers during the 10-30s first-time computation.

**Tech Stack:** Python 3.11, Flask 3, python-igraph 0.11, scipy.spatial.cKDTree, flask-limiter (rate limiting — plan originally specified slowapi but slowapi 0.1.9 is for Starlette/FastAPI, not Flask; flask-limiter has the API the plan code expected), gunicorn (production WSGI), shapely + pyproj (geometry), python-frontmatter (treatment markdown), sqlite3 (read-only `bikemap.db` + read-write `cache.db`).

**Module boundary rule (spec §5.1):** `app/` never imports from `prep/` and vice versa.

**Existing data shape (verified May 2026):**
- `bikemap.db` 69 MB: 353,645 streets, 307,448 intersections, 3,330 POIs, 5 treatments, 2,801 HIN features.
- Streets PK is `road_id` (PFB per-block ID). `head_node_osm_id`/`tail_node_osm_id` store PFB INTERSECTI/INTERSE_01 intersection IDs. Geometry is WKB LineString in EPSG:4326.
- Intersection PK `osm_id` is the same PFB intersection ID space as `streets.head_node_osm_id`/`tail_node_osm_id`. Geometry is WKB Point in EPSG:4326.
- Distance math uses EPSG:6454 (NAD83(2011) IL East, metres).
- Largest weak component: 296,047 / 307,448 vertices (96.3%). One giant component for routing.

**Spec sections this plan implements:** §0.1 (tier weights), §3.1 (web service), §3.5 (cache), §3.6 (POI selection rules), §3.10 (web service ops + memory budget), §3.13 (testing), §4.1 (cost function + max rule), §4.4 (best-effort fallback), §4.5 (gap analysis algorithm), §4.6 (multi-route aggregation — included for symmetry; called by frontend in Plan 2B).

**Out of scope (deferred):**
- Frontend (Plan 2B).
- Render deploy / Dockerfile / `make upload-db` tool (Plan 2C).
- Pickled-igraph startup optimization (recommended in spec §3.10 only if observed startup > 60s; current 34s is within window).
- A* over Dijkstra: Dijkstra at 200-450ms per cross-town route is within spec performance budget; A* in Python over igraph would have callback overhead that may not net out positive. Revisit if launch criterion §6.4#8 fails.
- HIN annotation counts in `/routes` payload (spec §4.3 UI strings: "Your fast route crosses N Cook County HIN intersections"). Backend has the data in `streets.on_hin` and `intersections.on_hin`; surfacing it as response fields is a 30-min patch when Plan 2B's frontend calls for it.
- Async LRU cache eviction worker (spec §3.5 — explicit deviation). Task 6 implements synchronous eviction-on-write; harmless until cache.db approaches 500 MB. **Plan 2C must convert to a background-thread worker before launch** to satisfy spec's "never block a user request on it."
- Multi-route aggregation (spec §4.6). The frontend computes the aggregate priority ranking client-side from per-pair gap results returned by `/gap-analysis`.
- Application-level coordinate-stripping log middleware (spec §3.8 defense-in-depth). Achieved in v1 by: (1) coordinates travel only in POST bodies, which are never in Flask's default access log format, and (2) we do not call `app.logger.info(...)` with coordinate arguments anywhere. Implementers MUST NOT log coordinate values in any handler.
- `/health` returning `503` during graph load (spec §3.10 — explicit deviation). Task 13's `create_app` is synchronous: Flask isn't bound to a port until the graph finishes loading (~35s), so external callers see a connection-refused, not a 503 response. Functionally equivalent for Render's health-check semantics — `initialDelaySeconds: 120` (spec §5.6) covers the load window. **Async startup with a background graph-loader thread and a `_graph_ready` flag is deferred to Plan 2C** if the deviation matters for any other consumer.

**File structure (created in this plan):**

```
chicago-bike-advocacy-map/
├── app/
│   ├── __init__.py
│   ├── main.py                  # Flask factory; /health; rate limiting
│   ├── core/
│   │   ├── __init__.py
│   │   ├── weights.py           # tier weight tables (single source from spec §0.1)
│   │   ├── graph.py             # GraphSnapshot loader + nearest-vertex KD-tree
│   │   ├── routing.py           # fast + safe + fallback shortest-path
│   │   ├── poi_picker.py        # POI table loader + nearest-by-category
│   │   ├── cache.py             # gap_cache schema + R/W + LRU eviction
│   │   └── gap_analysis.py      # detour zone, candidate scoring, corridor
│   └── routes/
│       ├── __init__.py
│       ├── geocode.py
│       ├── routing.py           # /routes
│       ├── pois.py              # /pois
│       ├── treatments.py        # /treatments/:slug
│       └── gap_analysis.py      # /gap-analysis + /gap-analysis/status
└── tests/app/
    ├── __init__.py
    ├── conftest.py              # shared fixtures (small bikemap.db, GraphSnapshot)
    ├── test_weights.py
    ├── test_graph.py
    ├── test_routing.py
    ├── test_poi_picker.py
    ├── test_cache.py
    ├── test_gap_analysis.py
    ├── test_routes_geocode.py
    ├── test_routes_routing.py
    ├── test_routes_pois.py
    ├── test_routes_treatments.py
    ├── test_routes_gap_analysis.py
    └── test_main.py
```

**Coordinate convention used throughout:** WGS84 (EPSG:4326) lat/lon as `(lat, lon)` tuples in user-facing API; `(x, y) = (lon, lat)` for shapely geometries; EPSG:6454 metres for any distance / buffer / KD-tree math.

**Test fixture strategy:** A tiny synthetic `bikemap.db` is built in `tests/app/conftest.py` using `prep.db.builder.DbBuilder` with hand-crafted segments forming a 5-node grid (LTS mix) plus a few POIs and treatments. Real Chicago `data/bikemap.db` is used only in the final integration smoke test (Task 14). This keeps unit tests fast (<1s each) without depending on the 69 MB production DB.

---

## Task 1: Setup — install dependencies, create app/ skeleton

**Files:**
- Modify: `chicago-bike-advocacy-map/pyproject.toml`
- Create: `chicago-bike-advocacy-map/app/__init__.py`
- Create: `chicago-bike-advocacy-map/app/core/__init__.py`
- Create: `chicago-bike-advocacy-map/app/routes/__init__.py`
- Create: `chicago-bike-advocacy-map/tests/app/__init__.py`

- [ ] **Step 1: Add runtime dependencies to pyproject.toml**

Find the `[project]` or `[tool.poetry.dependencies]` section in `pyproject.toml` and add (or merge with existing):

```toml
# Add to dependencies — these are NOT yet installed in the worktree's venv:
flask = ">=3.0,<4"
slowapi = ">=0.1.9"
gunicorn = ">=21.2,<23"
scipy = ">=1.11"
```

Note: `python-igraph`, `psutil`, `shapely`, `pyproj`, `requests`, `python-frontmatter` are already installed (verified `.venv/bin/pip list`). `python-frontmatter` is used by `prep.db.treatments_loader`; `app/` only reads the parsed `body_md` column, so no new frontmatter dep here.

- [ ] **Step 2: Install the new deps into the venv**

Run: `cd chicago-bike-advocacy-map && .venv/bin/pip install flask slowapi gunicorn scipy`
Expected: `Successfully installed ...` (or "Requirement already satisfied" for any pre-existing).

- [ ] **Step 3: Create empty package init files**

Create each file with a single docstring line:

```python
# app/__init__.py
"""Web service package — Flask backend, routing, gap analysis."""
```

```python
# app/core/__init__.py
"""Core domain logic — graph, routing, POIs, gap analysis, cache."""
```

```python
# app/routes/__init__.py
"""HTTP route handlers — thin layer; calls into app.core."""
```

```python
# tests/app/__init__.py
```

(empty for tests/app/__init__.py)

- [ ] **Step 4: Verify imports work**

Run: `cd chicago-bike-advocacy-map && .venv/bin/python -c "import app; import app.core; import app.routes; print('ok')"`
Expected: `ok`

- [ ] **Step 5: Commit**

```bash
git add chicago-bike-advocacy-map/pyproject.toml chicago-bike-advocacy-map/app/ chicago-bike-advocacy-map/tests/app/
git commit -m "chore(app): scaffold app/ tree and add web-service deps"
```

---

## Task 2: Routing weights config (`app/core/weights.py`)

**Files:**
- Create: `chicago-bike-advocacy-map/app/core/weights.py`
- Test: `chicago-bike-advocacy-map/tests/app/test_weights.py`

**Spec ref:** §0.1 (canonical safety tier definitions).

- [ ] **Step 1: Write the failing test**

Create `tests/app/test_weights.py`:

```python
"""Tier weight config sanity tests — single source from spec §0.1."""
from app.core.weights import (
    INF_WEIGHT,
    TIERS,
    main_weight_for,
    fallback_weight_for,
)


def test_three_tiers_defined() -> None:
    assert set(TIERS.keys()) == {"kid", "parent", "any"}


def test_kid_tier_blocks_lts2_and_lts3() -> None:
    assert main_weight_for("kid", 1) == 1.0
    assert main_weight_for("kid", 2) == INF_WEIGHT
    assert main_weight_for("kid", 3) == INF_WEIGHT


def test_parent_tier_allows_lts2_blocks_lts3() -> None:
    assert main_weight_for("parent", 1) == 1.0
    assert main_weight_for("parent", 2) == 1.2
    assert main_weight_for("parent", 3) == INF_WEIGHT


def test_any_tier_allows_all() -> None:
    assert main_weight_for("any", 1) == 1.0
    assert main_weight_for("any", 2) == 1.2
    assert main_weight_for("any", 3) == 1.5


def test_kid_fallback_strongly_penalizes_higher_lts() -> None:
    assert fallback_weight_for("kid", 1) == 1.0
    assert fallback_weight_for("kid", 2) == 5.0
    assert fallback_weight_for("kid", 3) == 20.0


def test_inf_weight_dominates_any_realistic_path_cost() -> None:
    """Routing detects 'no in-tier path' by checking whether any edge in the
    Dijkstra result has weight >= INF_WEIGHT. Sanity: INF_WEIGHT must dwarf
    any plausible weighted cost from an all-allowed path. Chicago's diameter
    is ~50 km × 1.5 max weight = 75 km of weighted cost; INF_WEIGHT=1e9 is
    13 orders of magnitude above that."""
    assert INF_WEIGHT > 1e8


def test_invalid_lts_raises() -> None:
    import pytest
    with pytest.raises(ValueError):
        main_weight_for("kid", 0)
    with pytest.raises(ValueError):
        main_weight_for("kid", 4)


def test_invalid_tier_raises() -> None:
    import pytest
    with pytest.raises(KeyError):
        main_weight_for("medium", 1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd chicago-bike-advocacy-map && .venv/bin/pytest tests/app/test_weights.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.core.weights'`

- [ ] **Step 3: Implement weights.py**

Create `app/core/weights.py`:

```python
"""Routing weight tables — single source for spec §0.1.

Tier names map to user-facing labels in the UI:
    "kid"    → "Safe for kid"     (LTS 1 only)
    "parent" → "Safe for parent"  (LTS 1-2)
    "any"    → "Not safe"         (LTS 1-3)

Main weights enforce hard tier cutoffs (∞ for disallowed LTS levels);
fallback weights from §0.1 are applied when the main-weight route returns
no path. Both tables read from this file so values cannot drift between
code and spec.

INF_WEIGHT detection: routing.py checks whether ANY edge in a Dijkstra
result has weight ≥ INF_WEIGHT (rather than thresholding total cost),
which is robust to long-but-legitimate paths whose summed weight could
otherwise approach a chosen threshold.
"""
from __future__ import annotations

# Hard-cutoff sentinel. Any edge weighted at INF_WEIGHT effectively bars
# routing through it. Routing detects "no in-tier path" by checking
# `any(weights[e] >= INF_WEIGHT for e in epath)` — never via summed-cost
# threshold (which can misfire on long routes).
INF_WEIGHT = 1e9

TIERS: dict[str, dict[str, list[float]]] = {
    "kid": {
        "main":     [1.0, INF_WEIGHT, INF_WEIGHT],
        "fallback": [1.0, 5.0, 20.0],
    },
    "parent": {
        "main":     [1.0, 1.2, INF_WEIGHT],
        "fallback": [1.0, 1.2, 10.0],
    },
    "any": {
        "main":     [1.0, 1.2, 1.5],
        "fallback": [1.0, 1.2, 1.5],
    },
}


def _validate_lts(lts: int) -> None:
    if lts not in (1, 2, 3):
        raise ValueError(f"lts must be 1, 2, or 3 (got {lts})")


def main_weight_for(tier: str, lts: int) -> float:
    _validate_lts(lts)
    return TIERS[tier]["main"][lts - 1]


def fallback_weight_for(tier: str, lts: int) -> float:
    _validate_lts(lts)
    return TIERS[tier]["fallback"][lts - 1]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd chicago-bike-advocacy-map && .venv/bin/pytest tests/app/test_weights.py -v`
Expected: 8 passed (including the renamed `test_inf_weight_dominates_any_realistic_path_cost`)

- [ ] **Step 5: Commit**

```bash
git add chicago-bike-advocacy-map/app/core/weights.py chicago-bike-advocacy-map/tests/app/test_weights.py
git commit -m "feat(app): tier weight config sourced from spec §0.1"
```

---

## Task 3: Graph snapshot loader (`app/core/graph.py`)

**Files:**
- Create: `chicago-bike-advocacy-map/app/core/graph.py`
- Test: `chicago-bike-advocacy-map/tests/app/test_graph.py`
- Create: `chicago-bike-advocacy-map/tests/app/conftest.py`

**Spec ref:** §3.10 (graph load on startup), §4.1 (max rule for effective_lts).

**Design notes:**
- `bikemap.db` is opened read-only via `sqlite3.connect("file:...?mode=ro", uri=True)`.
- Each street row becomes TWO directed igraph edges (forward + reverse). Bidirectional bike routing is the v1 default; one-way handling is deferred (the `OSM_ID` lookup carries the OSM way ID for future per-direction logic).
- Per spec §4.1: `effective_lts(edge) = max(segment_lts, head_node.lts_approach)`. "Head" = the node we are entering (the edge's destination).
- KD-tree of vertex coordinates uses EPSG:6454 (metres) so brute-force Euclidean nearest matches geodesic nearest at city-block scale.
- Both **main** and **fallback** per-tier edge weights are precomputed at load time (Fix 9) so neither route handlers nor gap analysis allocate them per request.
- Self-loops in `streets` (37 in current Chicago data — segments where head==tail) are skipped.
- **Naming wart (Fix 12):** `osm_id_to_vertex` is the dict from PFB intersection node ID → igraph vertex idx — the `osm_id` part of the name reflects the schema column it joins on (`intersections.osm_id`), not real OpenStreetMap node IDs. Same for `vertex_to_int_id` (the inverse). Documented in field docstrings; **do not rename** to keep parallelism with the schema.
- **In-memory street + intersection metadata (Fix 3):** loading `streets` and `intersections` ONCE into parallel numpy arrays at startup avoids the per-request full-table scan that gap analysis would otherwise do. ~25 MB extra resident memory; eliminates the need to thread `db_path` into `analyze_gap`.
- **`nearest_vertex` returns distance (Fix 8):** signature is `tuple[int, float]` so route handlers can reject queries that snap to a vertex >5 km away (e.g., user clicked outside Cook County). Distance is in EPSG:6454 metres.

- [ ] **Step 1: Write the conftest fixtures for synthetic DBs**

Create `tests/app/conftest.py`:

```python
"""Shared fixtures for app/ tests.

Two fixtures (Fix 5):

`tiny_bikemap_db` — 5-node grid, used by simple unit tests (graph loader,
basic routing). Has an LTS-3 chokepoint at v300 to exercise the max rule.

`divergent_bikemap_db` — 4-node graph specifically designed to force
fast/safe divergence at 'parent' tier for gap-analysis tests. The fast
route uses a direct LTS-3 segment; the safe route detours via two
LTS-1 segments.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from prep.db.builder import DbBuilder
from prep.lts.ingest import IntersectionRecord, SegmentRecord


def _seg(road_id: int, osm_id: int, head: int, tail: int, lts: int,
         coords: list[tuple[float, float]],
         highway: str = "residential") -> SegmentRecord:
    line = "LINESTRING(" + ", ".join(f"{x} {y}" for x, y in coords) + ")"
    return SegmentRecord(
        road_id=road_id,
        osm_id=osm_id,
        head_int_id=head,
        tail_int_id=tail,
        name=f"Test St {road_id}",
        lts=lts,
        highway=highway,
        speed=25,
        ft_int_str=lts,
        tf_int_str=lts,
        geometry_wkt=line,
        raw_properties={},
    )


def _intersection(int_id: int, lat: float, lon: float, lts_approach: int) -> IntersectionRecord:
    return IntersectionRecord(
        osm_id=int_id,
        lts_approach=lts_approach,
        signalized=None,
        lanes_crossed=None,
        geometry_wkt=f"POINT ({lon} {lat})",
        raw_properties={},
    )


@pytest.fixture
def tiny_bikemap_db(tmp_path: Path) -> Path:
    """5-node grid for graph + routing unit tests.

         v200 (LTS-1 approach)
          |
     v100-v300-v400  (v300 lts_approach=3 — the chokepoint)
          |
         v500

    Streets (all bidirectional after load):
      r1 v100 ↔ v300  lts=1
      r2 v300 ↔ v400  lts=1
      r3 v200 ↔ v300  lts=2
      r4 v300 ↔ v500  lts=3
      r5 v100 ↔ v500  lts=3   (alternate cross-grid edge)
    """
    db_path = tmp_path / "bikemap.db"
    builder = DbBuilder(db_path)
    builder.create_schema()
    builder.insert_intersections([
        _intersection(100, 41.940, -87.680, 1),
        _intersection(200, 41.945, -87.675, 1),
        _intersection(300, 41.940, -87.675, 3),  # chokepoint at LTS-3 approach
        _intersection(400, 41.940, -87.670, 1),
        _intersection(500, 41.935, -87.675, 1),
    ])
    builder.insert_streets([
        _seg(1, 1001, 100, 300, 1, [(-87.680, 41.940), (-87.675, 41.940)]),
        _seg(2, 1002, 300, 400, 1, [(-87.675, 41.940), (-87.670, 41.940)]),
        _seg(3, 1003, 200, 300, 2, [(-87.675, 41.945), (-87.675, 41.940)]),
        _seg(4, 1004, 300, 500, 3, [(-87.675, 41.940), (-87.675, 41.935)]),
        _seg(5, 1005, 100, 500, 3, [(-87.680, 41.940), (-87.675, 41.935)]),
    ])
    builder.record_schema_meta(code_version="test")
    builder.close()
    return db_path


@pytest.fixture
def divergent_bikemap_db(tmp_path: Path) -> Path:
    """4-node graph where fast and safe routes diverge at 'parent' tier.

         v100 ────[r1: lts=1, len 150m]────── v200
          │                                    │
       [r3: lts=3, len 200m]              [r2: lts=1, len 150m]
          │                                    │
         v300 ────[skipping; v100→v300→v400 forces diagonal]
                                                │
                                              v400

    Simpler: v100 → v400 with two paths:
      Direct: r3 (LTS 3, 200m).
      Detour: r1 + r2 (LTS 1 + LTS 1, total 300m).

    All intersections have lts_approach=1 (no chokepoint at the nodes).
    Tier behavior at v100 → v400:
      Fast: r3 (200m, ignores LTS).
      Safe-any: r3 weighted 200×1.5=300; detour weighted 150+150=300.
                Either-or; igraph picks one. Test tolerates both.
      Safe-parent: r3 blocked (LTS 3); detour OK. Diverges from fast.
                   Gap candidate r3 (LTS 3); hypothesizing r3.lts=2 yields
                   weighted 200×1.2=240, beating detour's 300. New safe
                   route uses r3, length 200m. Old safe length was 300m,
                   savings 100m. Headline.feature_id = r3.road_id.
      Safe-kid: r3 blocked (LTS 3 > kid); detour LTS 1 OK. Same divergence.
    """
    db_path = tmp_path / "bikemap.db"
    builder = DbBuilder(db_path)
    builder.create_schema()
    builder.insert_intersections([
        _intersection(10, 41.940, -87.680, 1),  # v100
        _intersection(20, 41.945, -87.675, 1),  # v200
        _intersection(40, 41.940, -87.670, 1),  # v400
    ])
    builder.insert_streets([
        # r1: v100 ↔ v200, LTS 1, ~150m (NW direction)
        _seg(101, 2001, 10, 20, 1, [(-87.680, 41.940), (-87.675, 41.945)]),
        # r2: v200 ↔ v400, LTS 1, ~150m (SE direction)
        _seg(102, 2002, 20, 40, 1, [(-87.675, 41.945), (-87.670, 41.940)]),
        # r3: v100 ↔ v400 direct, LTS 3, ~200m (E direction along latitude line)
        _seg(103, 2003, 10, 40, 3, [(-87.680, 41.940), (-87.670, 41.940)],
             highway="residential"),
    ])
    builder.record_schema_meta(code_version="test")
    builder.close()
    return db_path
```

- [ ] **Step 2: Write the failing graph-loader tests**

Create `tests/app/test_graph.py`:

```python
"""Tests for the graph-snapshot loader (app.core.graph)."""
from pathlib import Path

import pytest

from app.core.graph import GraphSnapshot, load_graph, nearest_vertex


def test_load_graph_creates_directed_graph(tiny_bikemap_db: Path) -> None:
    snap = load_graph(tiny_bikemap_db)
    assert isinstance(snap, GraphSnapshot)
    # 5 intersections → 5 vertices.
    assert snap.g.vcount() == 5
    # 5 streets × 2 directions → 10 directed edges.
    assert snap.g.ecount() == 10
    assert snap.g.is_directed()


def test_load_graph_maps_int_id_to_vertex_idx(tiny_bikemap_db: Path) -> None:
    snap = load_graph(tiny_bikemap_db)
    for int_id in (100, 200, 300, 400, 500):
        assert int_id in snap.osm_id_to_vertex
    # vertex_to_int_id is the inverse map (Fix 12).
    for int_id, vidx in snap.osm_id_to_vertex.items():
        assert snap.vertex_to_int_id[vidx] == int_id


def test_load_graph_effective_lts_applies_max_rule(tiny_bikemap_db: Path) -> None:
    """An edge entering v300 (lts_approach=3) should have effective_lts=3
    even when its segment_lts=1. (Spec §4.1 max rule.)"""
    snap = load_graph(tiny_bikemap_db)
    v100 = snap.osm_id_to_vertex[100]
    v300 = snap.osm_id_to_vertex[300]
    eid = snap.g.get_eid(v100, v300)
    assert snap.edge_seg_lts[eid] == 1
    assert snap.edge_head_lts[eid] == 3


def test_load_graph_reverse_edge_uses_reverse_head_lts(tiny_bikemap_db: Path) -> None:
    """The reverse edge v300 → v100 enters v100 (lts_approach=1)."""
    snap = load_graph(tiny_bikemap_db)
    v100 = snap.osm_id_to_vertex[100]
    v300 = snap.osm_id_to_vertex[300]
    eid_rev = snap.g.get_eid(v300, v100)
    assert snap.edge_seg_lts[eid_rev] == 1
    assert snap.edge_head_lts[eid_rev] == 1


def test_load_graph_precomputes_main_and_fallback_per_tier_weights(
    tiny_bikemap_db: Path,
) -> None:
    """Both main and fallback weights are precomputed at load (Fix 9)."""
    snap = load_graph(tiny_bikemap_db)
    assert set(snap.base_weights_by_tier.keys()) == {"kid", "parent", "any"}
    assert set(snap.fallback_weights_by_tier.keys()) == {"kid", "parent", "any"}
    for tier_weights in snap.base_weights_by_tier.values():
        assert len(tier_weights) == snap.g.ecount()
    for tier_weights in snap.fallback_weights_by_tier.values():
        assert len(tier_weights) == snap.g.ecount()


def test_load_graph_populates_in_memory_road_metadata(tiny_bikemap_db: Path) -> None:
    """Per-road_id arrays are loaded for in-memory gap-analysis filtering (Fix 3)."""
    snap = load_graph(tiny_bikemap_db)
    # 5 unique road_ids in the fixture.
    assert len(snap.road_id_array) == 5
    # road_id_to_idx maps each road_id to its array index.
    for road_id in (1, 2, 3, 4, 5):
        idx = snap.road_id_to_idx[road_id]
        assert snap.road_id_array[idx] == road_id
    # road_lts_array, road_length_array, road_bbox_proj are aligned.
    assert len(snap.road_lts_array) == 5
    assert len(snap.road_length_array) == 5
    assert snap.road_bbox_proj.shape == (5, 4)
    # Bbox bounds are sane: minx <= maxx, miny <= maxy.
    for i in range(5):
        minx, miny, maxx, maxy = snap.road_bbox_proj[i]
        assert minx <= maxx and miny <= maxy


def test_load_graph_populates_per_vertex_metadata(tiny_bikemap_db: Path) -> None:
    """Per-vertex arrays for gap-analysis intersection candidates (Fix 3)."""
    snap = load_graph(tiny_bikemap_db)
    assert len(snap.vertex_lts_approach) == snap.g.vcount()
    assert len(snap.vertex_on_hin) == snap.g.vcount()
    # v300 has lts_approach=3 in the fixture.
    v300 = snap.osm_id_to_vertex[300]
    assert snap.vertex_lts_approach[v300] == 3


def test_nearest_vertex_returns_idx_and_distance(tiny_bikemap_db: Path) -> None:
    snap = load_graph(tiny_bikemap_db)
    # Query at v300's coordinates exactly — distance ≈ 0.
    v_idx, dist_m = nearest_vertex(snap, 41.940, -87.675)
    assert v_idx == snap.osm_id_to_vertex[300]
    assert dist_m < 1.0  # within 1 metre


def test_nearest_vertex_distance_increases_with_offset(tiny_bikemap_db: Path) -> None:
    """Distance is in EPSG:6454 metres (Fix 8)."""
    snap = load_graph(tiny_bikemap_db)
    # ~100m offset NE of v100.
    _, dist = nearest_vertex(snap, 41.9402, -87.6798)
    assert 5.0 < dist < 200.0  # between 5m and 200m sanity range
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd chicago-bike-advocacy-map && .venv/bin/pytest tests/app/test_graph.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.core.graph'`

- [ ] **Step 4: Implement graph.py**

Create `app/core/graph.py`:

```python
"""Load the bikemap routing graph into igraph at startup.

`load_graph(db_path)` returns a GraphSnapshot bundling:
  - the directed graph (one vertex per intersection, two directed edges per
    street),
  - lookup maps (PFB int_id → vertex idx, and the inverse),
  - per-edge attribute arrays (segment_lts, head_node lts_approach, length_m,
    road_id, highway),
  - per-tier precomputed MAIN and FALLBACK edge weights (length × weight[
    tier, effective_lts]),
  - a scipy KD-tree of intersection coordinates in EPSG:6454 metres for
    nearest-vertex lookups returning (idx, distance_m),
  - per-vertex arrays (lts_approach, on_hin) and per-road_id arrays
    (lts, length, on_hin, highway, head/tail int_id, bbox in EPSG:6454)
    used by gap_analysis for in-memory candidate enumeration without
    DB access at request time.

Read-only; shared across all request threads (gunicorn `-w 1 --threads 4`)
and never mutated after construction.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

import igraph as ig
import numpy as np
from pyproj import Transformer
from scipy.spatial import cKDTree
from shapely import wkb

from app.core.weights import TIERS

_TO_IL_EAST_M = Transformer.from_crs("EPSG:4326", "EPSG:6454", always_xy=True).transform


@dataclass(frozen=True)
class GraphSnapshot:
    # Graph topology + per-edge attributes (one entry per directed edge,
    # E = 2 × number of bidirectional streets).
    g: ig.Graph
    edge_seg_lts: list[int]                       # 1..3
    edge_head_lts: list[int]                      # 1..3 (lts_approach of edge's destination)
    edge_length_m: list[float]
    edge_road_id: list[int]                       # source PFB ROAD_ID
    edge_highway: list[str | None]

    # Per-tier precomputed weights, length E. Both main and fallback so
    # routing.py and gap_analysis.py never allocate per-request (Fix 9).
    base_weights_by_tier: dict[str, list[float]]
    fallback_weights_by_tier: dict[str, list[float]]

    # Vertex-level data (length V).
    # Naming wart (Fix 12): osm_id_to_vertex / vertex_to_int_id refer to PFB's
    # intersection node IDs (the schema's `intersections.osm_id` column),
    # NOT real OpenStreetMap node IDs. Kept for parallelism with the schema.
    osm_id_to_vertex: dict[int, int]              # PFB int_id -> vertex idx
    vertex_to_int_id: list[int]                   # vertex idx -> PFB int_id
    vertex_coords_wgs84: list[tuple[float, float]]  # idx -> (lat, lon)
    vertex_coords_proj: np.ndarray                # shape (V, 2) EPSG:6454 metres
    vertex_kdtree: cKDTree
    vertex_lts_approach: np.ndarray               # shape (V,) int8
    vertex_on_hin: np.ndarray                     # shape (V,) bool

    # Per-unique-road_id metadata for in-memory gap-analysis filtering (Fix 3).
    # All arrays aligned to road_id_array order.
    road_id_array: np.ndarray                     # shape (R,) int64
    road_id_to_idx: dict[int, int]                # road_id -> array index
    road_osm_id_array: np.ndarray                 # shape (R,) int64
    road_lts_array: np.ndarray                    # shape (R,) int8
    road_length_array: np.ndarray                 # shape (R,) float64
    road_on_hin_array: np.ndarray                 # shape (R,) bool
    road_highway_list: list[str | None]           # length R
    road_head_int_id_array: np.ndarray            # shape (R,) int64
    road_tail_int_id_array: np.ndarray            # shape (R,) int64
    road_bbox_proj: np.ndarray                    # shape (R, 4) — minx, miny, maxx, maxy
    road_endpoints_proj: np.ndarray               # shape (R, 4) — head_x, head_y, tail_x, tail_y


def load_graph(db_path: Path) -> GraphSnapshot:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row

    # ---- Intersections → vertices ------------------------------------------------
    int_rows = list(con.execute(
        "SELECT osm_id, geom, lts_approach, on_hin FROM intersections"
    ))
    n_vertices = len(int_rows)
    osm_id_to_vertex: dict[int, int] = {}
    vertex_to_int_id: list[int] = [0] * n_vertices
    coords_wgs84: list[tuple[float, float]] = [(0.0, 0.0)] * n_vertices
    coords_proj = np.empty((n_vertices, 2), dtype=np.float64)
    vertex_lts_approach = np.empty(n_vertices, dtype=np.int8)
    vertex_on_hin = np.empty(n_vertices, dtype=bool)

    for idx, r in enumerate(int_rows):
        int_id = int(r["osm_id"])
        osm_id_to_vertex[int_id] = idx
        vertex_to_int_id[idx] = int_id
        pt = wkb.loads(r["geom"])
        coords_wgs84[idx] = (pt.y, pt.x)  # (lat, lon)
        x_m, y_m = _TO_IL_EAST_M(pt.x, pt.y)
        coords_proj[idx] = (x_m, y_m)
        vertex_lts_approach[idx] = int(r["lts_approach"])
        vertex_on_hin[idx] = bool(r["on_hin"])

    kdtree = cKDTree(coords_proj)

    # ---- Streets → directed edges + per-road_id arrays --------------------------
    sql = """
        SELECT road_id, osm_id, head_node_osm_id, tail_node_osm_id,
               length_m, lts, highway, on_hin, geom
          FROM streets
         WHERE head_node_osm_id != tail_node_osm_id
    """
    edges: list[tuple[int, int]] = []
    seg_lts: list[int] = []
    head_lts: list[int] = []
    length_m: list[float] = []
    road_id_per_edge: list[int] = []
    highway_per_edge: list[str | None] = []

    # Per-road_id (one entry per row, since each PFB ROAD_ID is unique per row).
    road_ids: list[int] = []
    road_osm: list[int] = []
    road_lts: list[int] = []
    road_lengths: list[float] = []
    road_on_hin: list[bool] = []
    road_highways: list[str | None] = []
    road_heads: list[int] = []
    road_tails: list[int] = []
    bboxes: list[tuple[float, float, float, float]] = []
    endpoints: list[tuple[float, float, float, float]] = []

    for r in con.execute(sql):
        h_int = int(r["head_node_osm_id"])
        t_int = int(r["tail_node_osm_id"])
        if h_int not in osm_id_to_vertex or t_int not in osm_id_to_vertex:
            continue  # Defensive; shouldn't happen with Plan 1 schema invariants.
        h = osm_id_to_vertex[h_int]
        t = osm_id_to_vertex[t_int]
        sl = int(r["lts"])
        ll = float(r["length_m"])
        rid = int(r["road_id"])
        hw = r["highway"]
        on_hin = bool(r["on_hin"])

        # Forward + reverse directed edges.
        edges.append((h, t))
        seg_lts.append(sl); head_lts.append(int(vertex_lts_approach[t]))
        length_m.append(ll); road_id_per_edge.append(rid); highway_per_edge.append(hw)

        edges.append((t, h))
        seg_lts.append(sl); head_lts.append(int(vertex_lts_approach[h]))
        length_m.append(ll); road_id_per_edge.append(rid); highway_per_edge.append(hw)

        # Per-road_id metadata + projected bbox/endpoints for gap analysis.
        line = wkb.loads(r["geom"])
        # Project all coordinates of the LineString to EPSG:6454 once.
        proj_coords = [_TO_IL_EAST_M(x, y) for (x, y) in line.coords]
        xs = [c[0] for c in proj_coords]
        ys = [c[1] for c in proj_coords]
        head_x, head_y = proj_coords[0]
        tail_x, tail_y = proj_coords[-1]

        road_ids.append(rid)
        road_osm.append(int(r["osm_id"]))
        road_lts.append(sl)
        road_lengths.append(ll)
        road_on_hin.append(on_hin)
        road_highways.append(hw)
        road_heads.append(h_int)
        road_tails.append(t_int)
        bboxes.append((min(xs), min(ys), max(xs), max(ys)))
        endpoints.append((head_x, head_y, tail_x, tail_y))

    con.close()

    g = ig.Graph(n=n_vertices, edges=edges, directed=True)

    # ---- Per-tier weight precomputation (Fix 9: main + fallback) ----------------
    base_weights_by_tier: dict[str, list[float]] = {}
    fallback_weights_by_tier: dict[str, list[float]] = {}
    for tier_name, tables in TIERS.items():
        main_w = tables["main"]
        fb_w = tables["fallback"]
        per_edge_main: list[float] = []
        per_edge_fb: list[float] = []
        for i in range(g.ecount()):
            eff = max(seg_lts[i], head_lts[i])
            per_edge_main.append(length_m[i] * main_w[eff - 1])
            per_edge_fb.append(length_m[i] * fb_w[eff - 1])
        base_weights_by_tier[tier_name] = per_edge_main
        fallback_weights_by_tier[tier_name] = per_edge_fb

    # ---- Convert per-road lists to numpy arrays ---------------------------------
    R = len(road_ids)
    road_id_array = np.asarray(road_ids, dtype=np.int64)
    road_id_to_idx = {int(rid): i for i, rid in enumerate(road_ids)}

    return GraphSnapshot(
        g=g,
        edge_seg_lts=seg_lts,
        edge_head_lts=head_lts,
        edge_length_m=length_m,
        edge_road_id=road_id_per_edge,
        edge_highway=highway_per_edge,
        base_weights_by_tier=base_weights_by_tier,
        fallback_weights_by_tier=fallback_weights_by_tier,
        osm_id_to_vertex=osm_id_to_vertex,
        vertex_to_int_id=vertex_to_int_id,
        vertex_coords_wgs84=coords_wgs84,
        vertex_coords_proj=coords_proj,
        vertex_kdtree=kdtree,
        vertex_lts_approach=vertex_lts_approach,
        vertex_on_hin=vertex_on_hin,
        road_id_array=road_id_array,
        road_id_to_idx=road_id_to_idx,
        road_osm_id_array=np.asarray(road_osm, dtype=np.int64),
        road_lts_array=np.asarray(road_lts, dtype=np.int8),
        road_length_array=np.asarray(road_lengths, dtype=np.float64),
        road_on_hin_array=np.asarray(road_on_hin, dtype=bool),
        road_highway_list=road_highways,
        road_head_int_id_array=np.asarray(road_heads, dtype=np.int64),
        road_tail_int_id_array=np.asarray(road_tails, dtype=np.int64),
        road_bbox_proj=np.asarray(bboxes, dtype=np.float64).reshape(R, 4) if R else np.empty((0, 4)),
        road_endpoints_proj=np.asarray(endpoints, dtype=np.float64).reshape(R, 4) if R else np.empty((0, 4)),
    )


def nearest_vertex(snap: GraphSnapshot, lat: float, lon: float) -> tuple[int, float]:
    """Return (vertex_idx, distance_m) of the nearest intersection.

    Distance is in EPSG:6454 metres. Callers should reject queries snapping
    to a vertex >5 km away (likely outside Cook County) — see Task 10.
    """
    x_m, y_m = _TO_IL_EAST_M(lon, lat)
    distance, idx = snap.vertex_kdtree.query([x_m, y_m], k=1)
    return int(idx), float(distance)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd chicago-bike-advocacy-map && .venv/bin/pytest tests/app/test_graph.py -v`
Expected: 9 passed

- [ ] **Step 6: Commit**

```bash
git add chicago-bike-advocacy-map/app/core/graph.py chicago-bike-advocacy-map/tests/app/test_graph.py chicago-bike-advocacy-map/tests/app/conftest.py
git commit -m "feat(app): GraphSnapshot loader with KD-tree and tier base weights"
```

---

## Task 4: Routing core (`app/core/routing.py`)

**Files:**
- Create: `chicago-bike-advocacy-map/app/core/routing.py`
- Test: `chicago-bike-advocacy-map/tests/app/test_routing.py`

**Spec ref:** §4.1 (cost function), §4.4 (best-effort fallback).

**Design notes:**
- v1 uses Dijkstra (`igraph.Graph.get_shortest_paths`) with the precomputed per-tier base weights. A* is deferred (see plan-level "Out of scope").
- Fast route uses raw `edge_length_m` as weights (LTS / HIN ignored per §4.1).
- Safe route uses `snap.base_weights_by_tier[tier]`. **Fallback detection (Fix 1):** after Dijkstra returns an edge_path, scan it — if any edge has weight ≥ `INF_WEIGHT`, the path crossed a disallowed-tier edge. Re-run Dijkstra with `snap.fallback_weights_by_tier[tier]` (precomputed at load — Fix 9) and flag `is_fallback=True`. Never use a summed-cost threshold; long-but-legitimate routes can sum high.
- Fast-route bike-routability filter (§4.1) is implicitly satisfied: PFB only emits LTS-evaluable bike-routable ways. Document this; don't re-filter.

- [ ] **Step 1: Write the failing routing tests**

Create `tests/app/test_routing.py`:

```python
"""Tests for app.core.routing — fast, safe, and fallback shortest paths."""
from pathlib import Path

from app.core.graph import load_graph, nearest_vertex
from app.core.routing import (
    Route,
    compute_fast_route,
    compute_safe_route,
)


def test_fast_route_minimizes_distance(tiny_bikemap_db: Path) -> None:
    """Fast route v100 → v400 should pass through v300 (the only direct path)."""
    snap = load_graph(tiny_bikemap_db)
    v100 = snap.osm_id_to_vertex[100]
    v400 = snap.osm_id_to_vertex[400]
    r = compute_fast_route(snap, v100, v400)
    assert r is not None
    assert isinstance(r, Route)
    assert r.vertex_path[0] == v100
    assert r.vertex_path[-1] == v400
    assert snap.osm_id_to_vertex[300] in r.vertex_path
    assert r.is_fallback is False
    assert r.length_m > 0


def test_safe_route_kid_tier_avoids_lts3(tiny_bikemap_db: Path) -> None:
    """v100 → v500: direct edge (road 5, LTS 3) is blocked at kid tier;
    must detour via v300, but v300 has lts_approach=3 — every entering edge
    is effectively LTS 3 → no in-tier path → fallback engages."""
    snap = load_graph(tiny_bikemap_db)
    v100 = snap.osm_id_to_vertex[100]
    v500 = snap.osm_id_to_vertex[500]
    r = compute_safe_route(snap, v100, v500, "kid")
    assert r is not None
    # Fallback expected because LTS-3 chokepoint at v300 + LTS-3 direct edge.
    assert r.is_fallback is True


def test_safe_route_any_tier_uses_shortest_lts3_allowed(tiny_bikemap_db: Path) -> None:
    """At 'any' tier (LTS 1-3 allowed with 1.5× penalty), v100 → v500 should
    use the direct LTS-3 edge (road 5) since the detour through v300 also hits LTS 3."""
    snap = load_graph(tiny_bikemap_db)
    v100 = snap.osm_id_to_vertex[100]
    v500 = snap.osm_id_to_vertex[500]
    r = compute_safe_route(snap, v100, v500, "any")
    assert r is not None
    assert r.is_fallback is False
    # Direct path = 2 vertices (v100, v500); detour via v300 = 3 vertices.
    assert len(r.vertex_path) == 2


def test_safe_route_records_lts_distribution(tiny_bikemap_db: Path) -> None:
    snap = load_graph(tiny_bikemap_db)
    v100 = snap.osm_id_to_vertex[100]
    v400 = snap.osm_id_to_vertex[400]
    r = compute_safe_route(snap, v100, v400, "any")
    assert r is not None
    assert sum(r.lts_distribution.values()) == len(r.edge_path)


def test_compute_routes_return_trivial_route_when_src_equals_dst(
    tiny_bikemap_db: Path,
) -> None:
    """Fix F: src == dst returns a Route with zero length and empty edge_path."""
    snap = load_graph(tiny_bikemap_db)
    v100 = snap.osm_id_to_vertex[100]
    fast = compute_fast_route(snap, v100, v100)
    assert fast is not None
    assert fast.length_m == 0.0
    assert fast.edge_path == []
    assert fast.vertex_path == [v100]
    safe = compute_safe_route(snap, v100, v100, "any")
    assert safe is not None
    assert safe.length_m == 0.0
    assert safe.is_fallback is False


def test_route_returns_none_for_unreachable_endpoints() -> None:
    """An isolated vertex should return None even at fallback weights."""
    # Build a 2-component graph manually.
    import sqlite3
    from pathlib import Path as _Path
    import tempfile

    from prep.db.builder import DbBuilder
    from prep.lts.ingest import IntersectionRecord, SegmentRecord

    with tempfile.TemporaryDirectory() as tmp:
        db_path = _Path(tmp) / "isolated.db"
        b = DbBuilder(db_path)
        b.create_schema()
        b.insert_intersections([
            IntersectionRecord(osm_id=1, lts_approach=1, signalized=None,
                               lanes_crossed=None, geometry_wkt="POINT(-87.7 41.9)",
                               raw_properties={}),
            IntersectionRecord(osm_id=2, lts_approach=1, signalized=None,
                               lanes_crossed=None, geometry_wkt="POINT(-87.6 41.9)",
                               raw_properties={}),
            IntersectionRecord(osm_id=3, lts_approach=1, signalized=None,
                               lanes_crossed=None, geometry_wkt="POINT(-87.5 41.9)",
                               raw_properties={}),
        ])
        b.insert_streets([
            SegmentRecord(road_id=1, osm_id=10, head_int_id=1, tail_int_id=2,
                          name=None, lts=1, highway="residential", speed=25,
                          ft_int_str=1, tf_int_str=1,
                          geometry_wkt="LINESTRING(-87.7 41.9, -87.6 41.9)",
                          raw_properties={}),
            # v3 has no edge — isolated vertex.
        ])
        b.record_schema_meta(code_version="test")
        b.close()

        snap = load_graph(db_path)
        v1 = snap.osm_id_to_vertex[1]
        v3 = snap.osm_id_to_vertex[3]
        assert compute_fast_route(snap, v1, v3) is None
        assert compute_safe_route(snap, v1, v3, "kid") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd chicago-bike-advocacy-map && .venv/bin/pytest tests/app/test_routing.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.core.routing'`

- [ ] **Step 3: Implement routing.py**

Create `app/core/routing.py`:

```python
"""Shortest-path routing on the GraphSnapshot.

Per spec §4.1:
  - Fast route: minimize edge_length_m only (LTS / HIN ignored). Bike-routability
    is implicit — PFB only emits LTS-evaluable bike-routable ways.
  - Safe route: minimize length × tier_weight[effective_lts] using main weights;
    if any edge in the result has weight >= INF_WEIGHT (i.e., the only path
    requires crossing a disallowed-tier edge), retry with fallback weights
    and flag is_fallback=True.

v1 uses Dijkstra (igraph.Graph.get_shortest_paths). A* is a deferred
optimization — see plan §"Out of scope".
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from app.core.graph import GraphSnapshot
from app.core.weights import INF_WEIGHT


@dataclass(frozen=True)
class Route:
    edge_path: list[int]               # igraph edge indices in order
    vertex_path: list[int]             # igraph vertex indices
    length_m: float                    # sum of edge_length_m along the path
    weighted_cost: float               # sum of weights along the path
    is_fallback: bool                  # True if main weights yielded no path
    lts_distribution: dict[int, int]   # effective_lts -> edge count


def _path_or_none(snap: GraphSnapshot, src: int, dst: int,
                   weights: list[float]) -> list[int] | None:
    paths = snap.g.get_shortest_paths(src, to=dst, weights=weights, output="epath")
    if not paths or not paths[0]:
        return None
    return paths[0]


def _build_route(snap: GraphSnapshot, edge_path: list[int],
                 weights: list[float], is_fallback: bool) -> Route:
    length = sum(snap.edge_length_m[e] for e in edge_path)
    cost = sum(weights[e] for e in edge_path)
    lts_hist: Counter[int] = Counter()
    for e in edge_path:
        eff = max(snap.edge_seg_lts[e], snap.edge_head_lts[e])
        lts_hist[eff] += 1
    vertices = [snap.g.es[edge_path[0]].source]
    for e in edge_path:
        vertices.append(snap.g.es[e].target)
    return Route(
        edge_path=list(edge_path),
        vertex_path=vertices,
        length_m=length,
        weighted_cost=cost,
        is_fallback=is_fallback,
        lts_distribution=dict(lts_hist),
    )


def _trivial_route(src: int) -> Route:
    """Zero-length route for the src == dst case (Fix F)."""
    return Route(
        edge_path=[],
        vertex_path=[src],
        length_m=0.0,
        weighted_cost=0.0,
        is_fallback=False,
        lts_distribution={},
    )


def compute_fast_route(snap: GraphSnapshot, src: int, dst: int) -> Route | None:
    """Minimize edge_length_m. LTS and HIN ignored (spec §4.1)."""
    if src == dst:
        return _trivial_route(src)
    weights = snap.edge_length_m
    epath = _path_or_none(snap, src, dst, weights)
    if epath is None:
        return None
    return _build_route(snap, epath, weights, is_fallback=False)


def compute_safe_route(snap: GraphSnapshot, src: int, dst: int, tier: str) -> Route | None:
    """Minimize stress-weighted distance for the given tier.

    Fallback detection (Fix 1): after Dijkstra returns a path, check whether
    ANY edge has weight >= INF_WEIGHT. If yes, the only path crosses a
    disallowed-tier edge — re-run with precomputed fallback weights from
    spec §0.1.
    """
    if src == dst:
        return _trivial_route(src)
    main_weights = snap.base_weights_by_tier[tier]
    epath = _path_or_none(snap, src, dst, main_weights)
    if epath is not None:
        if not any(main_weights[e] >= INF_WEIGHT for e in epath):
            return _build_route(snap, epath, main_weights, is_fallback=False)
        # Path crossed an INF edge → fall through to fallback.

    fallback_weights = snap.fallback_weights_by_tier[tier]
    epath_fb = _path_or_none(snap, src, dst, fallback_weights)
    if epath_fb is None:
        return None
    return _build_route(snap, epath_fb, fallback_weights, is_fallback=True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd chicago-bike-advocacy-map && .venv/bin/pytest tests/app/test_routing.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add chicago-bike-advocacy-map/app/core/routing.py chicago-bike-advocacy-map/tests/app/test_routing.py
git commit -m "feat(app): fast + safe + fallback shortest-path routing"
```

---

## Task 5: POI picker (`app/core/poi_picker.py`)

**Files:**
- Create: `chicago-bike-advocacy-map/app/core/poi_picker.py`
- Test: `chicago-bike-advocacy-map/tests/app/test_poi_picker.py`

**Spec ref:** §3.6 (POI selection rules — nearest by crow-flies per category).

**Design notes:**
- POIs loaded once at startup via `load_pois(db_path)` returning `dict[str, list[Poi]]` keyed by category. ~3,300 POIs in current data → ~700 KB resident.
- `nearest_poi(pois, lat, lon)` does a linear scan in projected EPSG:6454 metres — for ~500 POIs/category, that's ~50 µs per call. KD-tree per category is unnecessary overhead at this scale; revisit if categories grow.

- [ ] **Step 1: Extend `tests/app/conftest.py` to seed POIs into tiny_bikemap_db**

Append to `tests/app/conftest.py`:

```python
from prep.lts.ingest import PoiRecord


def _poi(name: str, category: str, lat: float, lon: float) -> PoiRecord:
    return PoiRecord(
        name=name,
        address=None,
        category=category,
        source="brokenspoke",
        geometry_wkt=f"POINT ({lon} {lat})",
        raw_properties={},
    )


@pytest.fixture
def tiny_bikemap_db_with_pois(tiny_bikemap_db: Path) -> Path:
    """tiny_bikemap_db plus a handful of POIs across categories."""
    builder = DbBuilder(tiny_bikemap_db)
    builder.insert_pois([
        _poi("Test Elementary", "school", 41.940, -87.671),    # near v400
        _poi("Far Elementary",  "school", 41.935, -87.675),    # near v500
        _poi("Test Park",       "park",   41.945, -87.675),    # near v200
        _poi("Solo Library",    "library", 41.940, -87.680),   # near v100
    ])
    builder.close()
    return tiny_bikemap_db
```

- [ ] **Step 2: Write the failing POI picker tests**

Create `tests/app/test_poi_picker.py`:

```python
"""Tests for app.core.poi_picker."""
from pathlib import Path

from app.core.poi_picker import Poi, load_pois, nearest_poi


def test_load_pois_groups_by_category(tiny_bikemap_db_with_pois: Path) -> None:
    pois_by_cat = load_pois(tiny_bikemap_db_with_pois)
    assert "school" in pois_by_cat
    assert "park" in pois_by_cat
    assert "library" in pois_by_cat
    assert len(pois_by_cat["school"]) == 2
    assert len(pois_by_cat["park"]) == 1


def test_nearest_poi_returns_closest_by_crow_flies(tiny_bikemap_db_with_pois: Path) -> None:
    pois_by_cat = load_pois(tiny_bikemap_db_with_pois)
    schools = pois_by_cat["school"]
    # Query near v400 (41.940, -87.670) — Test Elementary is at (41.940, -87.671), closer.
    nearest = nearest_poi(schools, 41.940, -87.670)
    assert nearest is not None
    assert nearest.name == "Test Elementary"


def test_nearest_poi_empty_list_returns_none() -> None:
    assert nearest_poi([], 41.94, -87.67) is None


def test_poi_dataclass_carries_lat_lon(tiny_bikemap_db_with_pois: Path) -> None:
    pois_by_cat = load_pois(tiny_bikemap_db_with_pois)
    p = pois_by_cat["library"][0]
    assert isinstance(p, Poi)
    assert p.lat == 41.940
    assert p.lon == -87.680
    assert p.category == "library"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd chicago-bike-advocacy-map && .venv/bin/pytest tests/app/test_poi_picker.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.core.poi_picker'`

- [ ] **Step 4: Implement poi_picker.py**

Create `app/core/poi_picker.py`:

```python
"""POI loading and nearest-by-category lookup (spec §3.6).

Loaded once at startup; ~3,300 POIs across 7+ categories ≈ 700 KB resident.
Linear scan per category for nearest-of-category queries is fine at this
scale.

Fix 10: Poi.x_m / y_m (EPSG:6454 projected coords) are precomputed at
load. nearest_poi avoids per-call pyproj transform calls — drops query
from ~23ms to <1ms at typical category sizes.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from pyproj import Transformer
from shapely import wkb

_TO_IL_EAST_M = Transformer.from_crs("EPSG:4326", "EPSG:6454", always_xy=True).transform


@dataclass(frozen=True)
class Poi:
    poi_id: int
    name: str | None
    address: str | None
    category: str
    source: str
    lat: float
    lon: float
    x_m: float           # EPSG:6454 metres (Fix 10 — precomputed)
    y_m: float


def load_pois(db_path: Path) -> dict[str, list[Poi]]:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    out: dict[str, list[Poi]] = {}
    for r in con.execute("SELECT id, name, address, category, source, geom FROM pois"):
        pt = wkb.loads(r["geom"])
        x_m, y_m = _TO_IL_EAST_M(pt.x, pt.y)
        out.setdefault(r["category"], []).append(Poi(
            poi_id=r["id"],
            name=r["name"],
            address=r["address"],
            category=r["category"],
            source=r["source"],
            lat=pt.y,
            lon=pt.x,
            x_m=x_m,
            y_m=y_m,
        ))
    con.close()
    return out


def nearest_poi(pois: list[Poi], lat: float, lon: float) -> Poi | None:
    """Return the POI nearest to (lat, lon) by crow-flies distance.
    Returns None if `pois` is empty. Uses precomputed Poi.x_m/y_m to avoid
    per-call projection overhead."""
    if not pois:
        return None
    qx, qy = _TO_IL_EAST_M(lon, lat)
    best: Poi | None = None
    best_d2 = float("inf")
    for p in pois:
        d2 = (p.x_m - qx) ** 2 + (p.y_m - qy) ** 2
        if d2 < best_d2:
            best_d2 = d2
            best = p
    return best
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd chicago-bike-advocacy-map && .venv/bin/pytest tests/app/test_poi_picker.py -v`
Expected: 4 passed

- [ ] **Step 6: Commit**

```bash
git add chicago-bike-advocacy-map/app/core/poi_picker.py chicago-bike-advocacy-map/tests/app/test_poi_picker.py chicago-bike-advocacy-map/tests/app/conftest.py
git commit -m "feat(app): POI loader + nearest-by-category picker"
```

---

## Task 6: Cache module (`app/core/cache.py`)

**Files:**
- Create: `chicago-bike-advocacy-map/app/core/cache.py`
- Test: `chicago-bike-advocacy-map/tests/app/test_cache.py`

**Spec ref:** §3.5 (gap analysis caching), §3.10 (cache.db lifecycle).

**Design notes:**
- `cache.db` is a separate SQLite file (writable; `bikemap.db` stays read-only).
- Cache key: SHA-256 of `f"{round(home_lat,5)},{round(home_lon,5)}|{round(dest_lat,5)},{round(dest_lon,5)}|{tier}"` — ~1m precision rounding for privacy.
- Fingerprint check: on init, compare stored fingerprint to current `bikemap.db` fingerprint (`schema_version + record_count`). Mismatch → truncate cache.
- LRU eviction: if `cache.db` exceeds 500 MB, delete oldest entries until under 400 MB. Implemented synchronously here; the spec wants it asynchronous (§3.5) but that's a Plan 2A.5 concern. Document the TODO.

- [ ] **Step 1: Write the failing cache tests**

Create `tests/app/test_cache.py`:

```python
"""Tests for app.core.cache — gap_cache schema, R/W, fingerprint check."""
from pathlib import Path

from app.core.cache import (
    bikemap_fingerprint,
    cache_key,
    get_cached_gap,
    init_cache_db,
    put_cached_gap,
)


def test_cache_key_deterministic() -> None:
    k1 = cache_key((41.9, -87.7), (41.88, -87.62), "kid")
    k2 = cache_key((41.9, -87.7), (41.88, -87.62), "kid")
    assert k1 == k2
    # Different tier → different key.
    k3 = cache_key((41.9, -87.7), (41.88, -87.62), "any")
    assert k1 != k3


def test_cache_key_hides_raw_coords() -> None:
    """Cache key should not contain raw lat/lon (spec §3.5: privacy)."""
    k = cache_key((41.9, -87.7), (41.88, -87.62), "kid")
    assert "41.9" not in k
    assert "87.7" not in k


def test_init_cache_db_creates_schema(tmp_path: Path) -> None:
    cache_path = tmp_path / "cache.db"
    init_cache_db(cache_path, fingerprint="abc123")
    assert cache_path.exists()
    # Re-init with same fingerprint preserves data.
    put_cached_gap(cache_path, "k1", {"foo": "bar"})
    init_cache_db(cache_path, fingerprint="abc123")
    assert get_cached_gap(cache_path, "k1") == {"foo": "bar"}


def test_init_cache_db_truncates_on_fingerprint_mismatch(tmp_path: Path) -> None:
    """Bumped bikemap.db schema/record_count → cache is wiped (spec §3.5)."""
    cache_path = tmp_path / "cache.db"
    init_cache_db(cache_path, fingerprint="abc123")
    put_cached_gap(cache_path, "k1", {"foo": "bar"})
    init_cache_db(cache_path, fingerprint="DIFFERENT")
    assert get_cached_gap(cache_path, "k1") is None


def test_put_then_get_roundtrips_json(tmp_path: Path) -> None:
    cache_path = tmp_path / "cache.db"
    init_cache_db(cache_path, fingerprint="x")
    payload = {"length_m": 7654.3, "headline": {"road_id": 42}}
    put_cached_gap(cache_path, "key1", payload)
    assert get_cached_gap(cache_path, "key1") == payload


def test_get_returns_none_for_unknown_key(tmp_path: Path) -> None:
    cache_path = tmp_path / "cache.db"
    init_cache_db(cache_path, fingerprint="x")
    assert get_cached_gap(cache_path, "missing") is None


def test_bikemap_fingerprint_combines_schema_version_and_record_count(
    tiny_bikemap_db: Path,
) -> None:
    fp = bikemap_fingerprint(tiny_bikemap_db)
    # Fingerprint format is opaque but stable.
    assert isinstance(fp, str)
    assert len(fp) > 0
    assert fp == bikemap_fingerprint(tiny_bikemap_db)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd chicago-bike-advocacy-map && .venv/bin/pytest tests/app/test_cache.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.core.cache'`

- [ ] **Step 3: Implement cache.py**

Create `app/core/cache.py`:

```python
"""Gap-analysis result cache (spec §3.5).

A separate writable SQLite DB (`cache.db`) so bikemap.db stays strictly
read-only in production. Cache keys are SHA-256 of rounded coordinates +
tier (privacy: raw addresses never persisted).

LRU eviction: if cache.db exceeds 500 MB, delete oldest entries until
size drops below 400 MB. Implemented synchronously; an async eviction
worker is a deferred optimization (spec §3.5 TODO).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

CACHE_SIZE_HIGH_BYTES = 500 * 1024 * 1024
CACHE_SIZE_LOW_BYTES = 400 * 1024 * 1024

_SCHEMA = """
CREATE TABLE IF NOT EXISTS gap_cache (
    key TEXT PRIMARY KEY,
    result_json TEXT NOT NULL,
    computed_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS cache_meta (
    bikemap_fingerprint TEXT PRIMARY KEY
);
"""


def cache_key(home: tuple[float, float], dest: tuple[float, float], tier: str) -> str:
    """SHA-256 of rounded(5-dec) coords + tier. Privacy-preserving (spec §3.5).
    home and dest are (lat, lon) tuples."""
    payload = (
        f"{round(home[0], 5)},{round(home[1], 5)}|"
        f"{round(dest[0], 5)},{round(dest[1], 5)}|"
        f"{tier}"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def bikemap_fingerprint(db_path: Path) -> str:
    """Stable fingerprint of bikemap.db: schema_version + sum of record counts.
    Used to detect when a new bikemap.db has been deployed (spec §3.5)."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        sv = con.execute("SELECT schema_version FROM schema_meta LIMIT 1").fetchone()
        schema_version = sv[0] if sv else "unknown"
        # Sum of all per-source record counts — captures data refreshes too.
        rows = con.execute("SELECT source, record_count FROM meta").fetchall()
        rc_sum = sum(int(r[1]) for r in rows)
    finally:
        con.close()
    return f"v{schema_version}-rc{rc_sum}"


def init_cache_db(cache_path: Path, fingerprint: str) -> None:
    """Create cache.db if missing; truncate if stored fingerprint != current."""
    con = sqlite3.connect(cache_path)
    try:
        con.executescript(_SCHEMA)
        row = con.execute("SELECT bikemap_fingerprint FROM cache_meta LIMIT 1").fetchone()
        stored = row[0] if row else None
        if stored != fingerprint:
            con.execute("DELETE FROM gap_cache")
            con.execute("DELETE FROM cache_meta")
            con.execute("INSERT INTO cache_meta (bikemap_fingerprint) VALUES (?)",
                        (fingerprint,))
            con.commit()
    finally:
        con.close()


def get_cached_gap(cache_path: Path, key: str) -> dict | None:
    if not cache_path.exists():
        return None
    con = sqlite3.connect(f"file:{cache_path}?mode=ro", uri=True)
    try:
        row = con.execute(
            "SELECT result_json FROM gap_cache WHERE key = ?", (key,)
        ).fetchone()
    finally:
        con.close()
    if row is None:
        return None
    return json.loads(row[0])


def put_cached_gap(cache_path: Path, key: str, result: dict) -> None:
    """Insert or replace cache entry. computed_at = current time (unix sec).
    Triggers LRU eviction if cache.db exceeds CACHE_SIZE_HIGH_BYTES."""
    import time
    con = sqlite3.connect(cache_path)
    try:
        con.execute(
            "INSERT OR REPLACE INTO gap_cache (key, result_json, computed_at) "
            "VALUES (?, ?, ?)",
            (key, json.dumps(result), int(time.time())),
        )
        con.commit()
    finally:
        con.close()
    # Synchronous eviction check. Spec §3.5 prefers async; deferred.
    if cache_path.stat().st_size > CACHE_SIZE_HIGH_BYTES:
        _evict_lru(cache_path)


def _evict_lru(cache_path: Path) -> None:
    """Delete oldest gap_cache rows in batches until size drops below
    CACHE_SIZE_LOW_BYTES. VACUUM runs once at the end (Fix 11) — VACUUM
    rewrites the entire DB, so calling it per batch multiplies wall-clock
    cost by the number of iterations.

    Note: SQLite's reported file size doesn't shrink until VACUUM, so we
    estimate post-eviction size from row count and avoid the size loop.

    Approximation caveat (Fix D): rows-to-drop is estimated from average
    row size; variance can leave the post-VACUUM file slightly above
    CACHE_SIZE_LOW_BYTES, in which case the next write triggers a tiny
    follow-up eviction. Converges; over-eviction by a few rows is benign.
    """
    target_bytes = CACHE_SIZE_LOW_BYTES
    con = sqlite3.connect(cache_path)
    try:
        # Approximate average row size to estimate how many to delete.
        cur = con.execute("SELECT COUNT(*), COALESCE(SUM(LENGTH(result_json)), 0) FROM gap_cache")
        n_rows, total_json_bytes = cur.fetchone()
        if n_rows == 0:
            return
        # Add ~50 bytes/row for key + computed_at + index overhead.
        est_avg_row = (total_json_bytes / n_rows) + 50
        current_bytes = cache_path.stat().st_size
        bytes_to_drop = max(0, current_bytes - target_bytes)
        rows_to_drop = int(bytes_to_drop / est_avg_row) + 1
        if rows_to_drop <= 0:
            return
        con.execute(
            "DELETE FROM gap_cache WHERE key IN ("
            "  SELECT key FROM gap_cache ORDER BY computed_at ASC LIMIT ?"
            ")",
            (rows_to_drop,),
        )
        con.commit()
        con.execute("VACUUM")
    finally:
        con.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd chicago-bike-advocacy-map && .venv/bin/pytest tests/app/test_cache.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add chicago-bike-advocacy-map/app/core/cache.py chicago-bike-advocacy-map/tests/app/test_cache.py
git commit -m "feat(app): cache.db schema + R/W with fingerprint-based reset"
```

---

## Task 7: Gap analysis (`app/core/gap_analysis.py`)

**Files:**
- Create: `chicago-bike-advocacy-map/app/core/gap_analysis.py`
- Test: `chicago-bike-advocacy-map/tests/app/test_gap_analysis.py`

**Spec ref:** §4.5 (gap analysis algorithm + corridor detection), §4.6 (multi-route aggregation interface).

**Design notes:**
- Algorithm pseudocode in §4.5 is followed exactly: fast + safe routes → cases (no gap, fallback, divergent) → detour zone (convex hull + 200m buffer) → candidate filtering → top-100 sorted by `(feature_lts - tier_max_lts)` → per-candidate hypothetical recompute → rank by savings → corridor union-find.
- **Memory rule (critical):** never copy the graph. Per candidate, compute a fresh `weights` list (one Python list of floats, length = `g.ecount()`); for a segment candidate only ONE forward + reverse edge pair changes; for an intersection candidate, all in-edges of that vertex change. Pass the new weights list to `g.get_shortest_paths`. Graph topology stays shared.
- **Fix 3 — In-memory candidate enumeration:** uses `GraphSnapshot`'s precomputed `road_bbox_proj`, `road_lts_array`, `vertex_coords_proj`, `vertex_lts_approach` arrays. **No DB access at request time.** `analyze_gap` no longer takes `db_path`.
- **Fix 2 — Unified candidate sort:** segments and intersections are merged into one list, sorted by `(-(feature_lts - tier_max_lts), -length)` together (intersections use `length=0` as tie-breaker — they always lose ties to segments at the same violation level), then capped at 100. The previous design's separate-sort-then-concat would silently drop all intersections when there were >100 segment candidates.
- **Fix 1 — INF_WEIGHT detection:** the per-candidate Dijkstra also checks `any(weights[e] >= INF_WEIGHT)` to detect "this candidate's hypothetical fix doesn't unblock the path." Same logic as routing.py.
- Detour zone math uses shapely in EPSG:6454 metres (200m buffer is a metre value).
- Corridor adjacency: union-find over candidates whose buffered geometries intersect each other within 50 m.
- Tier max LTS: `kid` → 1, `parent` → 2, `any` → 3 (derived from `TIERS`).
- **v1 simplification (Fix C):** segment geometry is reconstructed from head/tail intersection coords (a 2-point straight chord), NOT the full PFB LineString. The bbox filter still uses the true min/max over all coords (precomputed at load), so missing curves only matters for the precise zone-intersection test on candidates near the zone boundary — a thin shell of false-rejections at most. PFB's per-block segments are typically <300m and 2-vertex; this approximation rarely matters in practice. Switch to full multi-vertex geometry if a v2 use case requires it.

- [ ] **Step 1: Write the failing gap-analysis tests**

Create `tests/app/test_gap_analysis.py`:

```python
"""Tests for app.core.gap_analysis. Uses divergent_bikemap_db fixture
which is specifically designed to force fast/safe divergence at 'parent'
tier (Fix 5)."""
from pathlib import Path

from app.core.graph import load_graph
from app.core.gap_analysis import GapResult, analyze_gap


def test_gap_no_diverge_yields_empty_headline(divergent_bikemap_db: Path) -> None:
    """At 'kid' tier, the LTS-3 direct edge is blocked AND the LTS-1 detour
    works: fast route uses direct (length-only), safe route uses detour.
    They diverge — but if 'kid' tier here the safe route is feasible, gap
    analysis runs. If we instead pick a route where safe == fast, headline
    is None. Use src=dst as the simplest no-diverge case (path length 0)."""
    snap = load_graph(divergent_bikemap_db)
    v10 = snap.osm_id_to_vertex[10]
    res = analyze_gap(snap, v10, v10, "any")
    assert isinstance(res, GapResult)
    assert res.headline is None


def test_gap_parent_tier_finds_lts3_segment_as_headline(
    divergent_bikemap_db: Path,
) -> None:
    """At parent tier (LTS 1-2 allowed), v100 → v400 fast = r3 (LTS 3, 200m);
    safe = r1 + r2 (LTS 1, 300m). Hypothetically downgrading r3 to LTS 2
    gives a 200m route — savings 100m. r3 must be the headline candidate."""
    snap = load_graph(divergent_bikemap_db)
    v100 = snap.osm_id_to_vertex[10]
    v400 = snap.osm_id_to_vertex[40]
    res = analyze_gap(snap, v100, v400, "parent")
    assert res.safe_route_is_fallback is False
    assert res.headline is not None
    assert res.headline.feature_kind == "segment"
    assert res.headline.feature_id == 103   # r3's road_id
    assert res.headline.current_lts == 3
    assert res.headline.savings_m > 50      # ~100m savings; allow slop


def test_gap_kid_tier_no_chokepoint_returns_in_tier_diverge(
    divergent_bikemap_db: Path,
) -> None:
    """At 'kid' tier: r3 (LTS 3) blocked, but r1+r2 (LTS 1) is in-tier.
    fast = r3 (length-only), safe = r1+r2. Diverge → r3 headline.
    Same shape as parent-tier but tighter tier — verifies the algorithm
    handles 'kid' identically when the detour exists."""
    snap = load_graph(divergent_bikemap_db)
    v100 = snap.osm_id_to_vertex[10]
    v400 = snap.osm_id_to_vertex[40]
    res = analyze_gap(snap, v100, v400, "kid")
    assert res.safe_route_is_fallback is False
    assert res.headline is not None
    assert res.headline.feature_id == 103   # r3 again


def test_gap_returns_no_headline_when_safe_route_is_fallback(
    tiny_bikemap_db: Path,
) -> None:
    """When safe route is fallback, gap analysis returns no per-destination
    candidate (spec §4.5 case 1: 'unreachable safely')."""
    snap = load_graph(tiny_bikemap_db)
    v100 = snap.osm_id_to_vertex[100]
    v500 = snap.osm_id_to_vertex[500]
    # tiny_bikemap_db at 'kid' tier: v300 lts_approach=3 chokepoint;
    # direct LTS-3 edge to v500 also blocked → fallback engages.
    res = analyze_gap(snap, v100, v500, "kid")
    assert res.safe_route_is_fallback is True
    assert res.headline is None


def test_gap_supporting_and_corridor_are_lists(
    divergent_bikemap_db: Path,
) -> None:
    """GapResult shape: supporting and corridor are lists (possibly empty)."""
    snap = load_graph(divergent_bikemap_db)
    v100 = snap.osm_id_to_vertex[10]
    v400 = snap.osm_id_to_vertex[40]
    res = analyze_gap(snap, v100, v400, "parent")
    assert isinstance(res.supporting, list)
    assert isinstance(res.corridor, list)


def test_gap_candidate_sort_combines_segments_and_intersections(
    divergent_bikemap_db: Path,
) -> None:
    """Fix 2 regression guard: when both segment and intersection candidates
    exist with different violation levels, the higher-violation one wins
    regardless of feature kind. divergent_bikemap_db has only segment
    candidates (all intersections have lts_approach=1) — this test simply
    asserts the candidate list is sorted by violation level descending."""
    snap = load_graph(divergent_bikemap_db)
    v100 = snap.osm_id_to_vertex[10]
    v400 = snap.osm_id_to_vertex[40]
    res = analyze_gap(snap, v100, v400, "parent")
    if res.headline is not None and res.supporting:
        prev_violation = res.headline.current_lts
        for c in res.supporting:
            # Each subsequent candidate has equal-or-lower violation.
            assert c.current_lts <= prev_violation
            prev_violation = c.current_lts
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd chicago-bike-advocacy-map && .venv/bin/pytest tests/app/test_gap_analysis.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.core.gap_analysis'`

- [ ] **Step 3: Implement gap_analysis.py**

Create `app/core/gap_analysis.py`:

```python
"""Gap analysis algorithm (spec §4.5) + corridor detection.

Inputs: GraphSnapshot, src/dst vertex indices, tier name.
Output: GapResult with fast_route, safe_route, ranked candidates, and
optional corridor grouping.

Memory rule: never copy the graph. Per candidate, build a fresh weights
list (length = ecount); only entries for affected edges change from the
precomputed base_weights. Pass to igraph.get_shortest_paths().

Fix 3: candidate enumeration uses GraphSnapshot's in-memory road/vertex
arrays — no DB access at request time.

Fix 2: segments and intersections are merged into one list and sorted
together by (-violation, -length) — intersections use length=0 as the
tie-breaker, so a higher-violation intersection always outranks a
lower-violation segment regardless of feature kind.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from pyproj import Transformer
from shapely.geometry import LineString, Point
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform, unary_union

from app.core.graph import GraphSnapshot
from app.core.routing import Route, compute_fast_route, compute_safe_route
from app.core.weights import INF_WEIGHT, main_weight_for

DETOUR_BUFFER_M = 200.0
MAX_CANDIDATES = 100
CORRIDOR_ADJACENCY_M = 50.0
CORRIDOR_RELATIVE_THRESHOLD = 0.5  # candidate must save >=50% of headline

_TIER_MAX_LTS = {"kid": 1, "parent": 2, "any": 3}
_INFEASIBLE_HIGHWAYS = {"motorway", "motorway_link", "trunk", "trunk_link",
                        "railway", "aerialway", "waterway"}

_TO_IL_EAST_M = Transformer.from_crs("EPSG:4326", "EPSG:6454", always_xy=True).transform


@dataclass(frozen=True)
class GapCandidate:
    feature_kind: str       # "segment" | "intersection"
    feature_id: int         # road_id (segment) or int_id (intersection)
    current_lts: int
    savings_m: float
    on_hin: bool
    geometry_wkt: str       # for frontend display (in EPSG:4326)


@dataclass(frozen=True)
class GapResult:
    fast_route: Route | None
    safe_route: Route | None
    safe_route_is_fallback: bool
    headline: GapCandidate | None
    supporting: list[GapCandidate]   # ranks 2-5
    corridor: list[GapCandidate]


def _route_geometry_wgs84(snap: GraphSnapshot, route: Route) -> LineString:
    coords = [(snap.vertex_coords_wgs84[v][1], snap.vertex_coords_wgs84[v][0])
              for v in route.vertex_path]
    return LineString(coords)


def _detour_zone_proj(snap: GraphSnapshot, fast: Route, safe: Route) -> BaseGeometry:
    fast_geom = _route_geometry_wgs84(snap, fast)
    safe_geom = _route_geometry_wgs84(snap, safe)
    union = unary_union([fast_geom, safe_geom])
    proj = transform(_TO_IL_EAST_M, union)
    return proj.convex_hull.buffer(DETOUR_BUFFER_M)


def _enumerate_candidates(
    snap: GraphSnapshot, zone_proj: BaseGeometry, tier_max_lts: int,
) -> list[dict]:
    """In-memory candidate enumeration (Fix 3). Returns a unified list of
    candidate dicts; segments and intersections are merged for unified sort."""
    zminx, zminy, zmaxx, zmaxy = zone_proj.bounds
    candidates: list[dict] = []

    # ---- Segments (filter by lts > tier_max_lts and bbox overlap) ----
    bb = snap.road_bbox_proj  # shape (R, 4)
    if bb.shape[0] > 0:
        bbox_overlap = (
            (bb[:, 2] >= zminx) & (bb[:, 0] <= zmaxx) &
            (bb[:, 3] >= zminy) & (bb[:, 1] <= zmaxy)
        )
        lts_violates = snap.road_lts_array > tier_max_lts
        idx = np.where(bbox_overlap & lts_violates)[0]
        for i in idx:
            highway = snap.road_highway_list[i]
            if highway in _INFEASIBLE_HIGHWAYS:
                continue
            # Precise intersection test using projected endpoint LineString.
            hx, hy, tx, ty = snap.road_endpoints_proj[i]
            line = LineString([(hx, hy), (tx, ty)])
            if not zone_proj.intersects(line):
                continue
            # Reconstruct WGS84 LineString for frontend display.
            head_v = snap.osm_id_to_vertex.get(int(snap.road_head_int_id_array[i]))
            tail_v = snap.osm_id_to_vertex.get(int(snap.road_tail_int_id_array[i]))
            if head_v is None or tail_v is None:
                continue
            hlat, hlon = snap.vertex_coords_wgs84[head_v]
            tlat, tlon = snap.vertex_coords_wgs84[tail_v]
            wgs_geom = LineString([(hlon, hlat), (tlon, tlat)])

            candidates.append({
                "feature_kind": "segment",
                "feature_id": int(snap.road_id_array[i]),
                "current_lts": int(snap.road_lts_array[i]),
                "length_m": float(snap.road_length_array[i]),  # tie-breaker
                "on_hin": bool(snap.road_on_hin_array[i]),
                "geometry_wkt": wgs_geom.wkt,
            })

    # ---- Intersections (filter by lts_approach > tier_max_lts and bbox) ----
    vp = snap.vertex_coords_proj  # shape (V, 2)
    if vp.shape[0] > 0:
        bbox_overlap = (
            (vp[:, 0] >= zminx) & (vp[:, 0] <= zmaxx) &
            (vp[:, 1] >= zminy) & (vp[:, 1] <= zmaxy)
        )
        lts_violates = snap.vertex_lts_approach > tier_max_lts
        idx = np.where(bbox_overlap & lts_violates)[0]
        for v in idx:
            x, y = vp[v]
            if not zone_proj.intersects(Point(float(x), float(y))):
                continue
            lat, lon = snap.vertex_coords_wgs84[v]
            candidates.append({
                "feature_kind": "intersection",
                "feature_id": snap.vertex_to_int_id[v],
                "current_lts": int(snap.vertex_lts_approach[v]),
                "length_m": 0.0,    # tie-break LAST among same-violation features
                "on_hin": bool(snap.vertex_on_hin[v]),
                "geometry_wkt": Point(lon, lat).wkt,
            })

    # Unified sort by (-violation, -length) — Fix 2.
    candidates.sort(key=lambda c: (
        -(c["current_lts"] - tier_max_lts),
        -c["length_m"],
    ))
    return candidates[:MAX_CANDIDATES]


def _hypothesize_segment_weights(
    snap: GraphSnapshot, base_weights: list[float], road_id: int,
    tier: str, tier_max_lts: int,
) -> list[float]:
    """Recompute weights for every directed edge sharing `road_id`, as if
    its segment_lts were tier_max_lts."""
    weights = list(base_weights)
    for eid, rid in enumerate(snap.edge_road_id):
        if rid != road_id:
            continue
        new_eff = max(tier_max_lts, snap.edge_head_lts[eid])
        weights[eid] = snap.edge_length_m[eid] * main_weight_for(tier, new_eff)
    return weights


def _hypothesize_intersection_weights(
    snap: GraphSnapshot, base_weights: list[float], int_id: int,
    tier: str, tier_max_lts: int,
) -> list[float]:
    """Recompute weights for every edge ENTERING `int_id`, as if the head
    node's lts_approach were tier_max_lts."""
    weights = list(base_weights)
    if int_id not in snap.osm_id_to_vertex:
        return weights
    v = snap.osm_id_to_vertex[int_id]
    for eid in snap.g.incident(v, mode="in"):
        new_eff = max(snap.edge_seg_lts[eid], tier_max_lts)
        weights[eid] = snap.edge_length_m[eid] * main_weight_for(tier, new_eff)
    return weights


def _safe_route_length(
    snap: GraphSnapshot, src: int, dst: int, weights: list[float],
) -> float | None:
    """Dijkstra with custom weights. Returns path length in metres, or None
    if no path or path crosses INF_WEIGHT (Fix 1)."""
    paths = snap.g.get_shortest_paths(src, to=dst, weights=weights, output="epath")
    if not paths or not paths[0]:
        return None
    epath = paths[0]
    if any(weights[e] >= INF_WEIGHT for e in epath):
        return None
    return sum(snap.edge_length_m[e] for e in epath)


def _detect_corridor(candidates: list[GapCandidate], top_k: int = 5) -> list[GapCandidate]:
    """Group top-k candidates that are within CORRIDOR_ADJACENCY_M (50m)
    of each other and have savings >= CORRIDOR_RELATIVE_THRESHOLD of headline.
    Returns the corridor (or empty if no group of ≥2 forms)."""
    if len(candidates) < 2:
        return []
    headline = candidates[0]
    threshold = headline.savings_m * CORRIDOR_RELATIVE_THRESHOLD
    from shapely import wkt as _wkt
    geoms_proj: list[BaseGeometry] = []
    for c in candidates[:top_k]:
        g = _wkt.loads(c.geometry_wkt)
        geoms_proj.append(transform(_TO_IL_EAST_M, g))

    in_corridor = [False] * len(geoms_proj)
    in_corridor[0] = True
    changed = True
    while changed:
        changed = False
        for i in range(1, len(geoms_proj)):
            if in_corridor[i] or candidates[i].savings_m < threshold:
                continue
            for j in range(len(geoms_proj)):
                if not in_corridor[j]:
                    continue
                if geoms_proj[i].buffer(CORRIDOR_ADJACENCY_M).intersects(geoms_proj[j]):
                    in_corridor[i] = True
                    changed = True
                    break
    members = [candidates[i] for i in range(len(geoms_proj)) if in_corridor[i]]
    return members if len(members) >= 2 else []


def analyze_gap(
    snap: GraphSnapshot, src: int, dst: int, tier: str,
) -> GapResult:
    """Run the spec §4.5 gap algorithm. Returns GapResult.

    No DB access at request time (Fix 3) — uses GraphSnapshot's in-memory
    road/vertex arrays.
    """
    fast = compute_fast_route(snap, src, dst)
    safe = compute_safe_route(snap, src, dst, tier)

    if safe is None or safe.is_fallback:
        return GapResult(
            fast_route=fast, safe_route=safe,
            safe_route_is_fallback=(safe.is_fallback if safe else True),
            headline=None, supporting=[], corridor=[],
        )

    if fast is None or fast.edge_path == safe.edge_path:
        return GapResult(
            fast_route=fast, safe_route=safe, safe_route_is_fallback=False,
            headline=None, supporting=[], corridor=[],
        )

    tier_max_lts = _TIER_MAX_LTS[tier]
    zone = _detour_zone_proj(snap, fast, safe)
    candidates = _enumerate_candidates(snap, zone, tier_max_lts)

    base_weights = snap.base_weights_by_tier[tier]
    current_safe_length = safe.length_m

    scored: list[GapCandidate] = []
    for c in candidates:
        if c["feature_kind"] == "segment":
            new_weights = _hypothesize_segment_weights(
                snap, base_weights, c["feature_id"], tier, tier_max_lts,
            )
        else:
            new_weights = _hypothesize_intersection_weights(
                snap, base_weights, c["feature_id"], tier, tier_max_lts,
            )

        new_length = _safe_route_length(snap, src, dst, new_weights)
        if new_length is None:
            continue
        savings = current_safe_length - new_length
        if savings <= 0:
            continue
        scored.append(GapCandidate(
            feature_kind=c["feature_kind"],
            feature_id=c["feature_id"],
            current_lts=c["current_lts"],
            savings_m=savings,
            on_hin=c["on_hin"],
            geometry_wkt=c["geometry_wkt"],
        ))

    scored.sort(key=lambda c: -c.savings_m)
    headline = scored[0] if scored else None
    supporting = scored[1:5]
    corridor = _detect_corridor(scored)

    return GapResult(
        fast_route=fast, safe_route=safe, safe_route_is_fallback=False,
        headline=headline, supporting=supporting, corridor=corridor,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd chicago-bike-advocacy-map && .venv/bin/pytest tests/app/test_gap_analysis.py -v`
Expected: 6 passed

(`analyze_gap` no longer takes `db_path` — Fix 3.)

- [ ] **Step 5: Commit**

```bash
git add chicago-bike-advocacy-map/app/core/gap_analysis.py chicago-bike-advocacy-map/tests/app/test_gap_analysis.py
git commit -m "feat(app): gap analysis with detour zone, candidate scoring, corridor"
```

---

## Task 8: `/treatments/:slug` route (`app/routes/treatments.py`)

**Files:**
- Create: `chicago-bike-advocacy-map/app/routes/treatments.py`
- Test: `chicago-bike-advocacy-map/tests/app/test_routes_treatments.py`

**Spec ref:** §4.3 (treatments — markdown library used in fact panels).

**Design notes:**
- Reads from the `treatments` table populated by Plan 1's `prep.db.treatments_loader`.
- 5 treatments in current data → fits in memory; load once and serve from a dict.

- [ ] **Step 1: Write the failing route tests**

Create `tests/app/test_routes_treatments.py`:

```python
"""Tests for /treatments/:slug route."""
from pathlib import Path

import pytest


@pytest.fixture
def treatments_app(tiny_bikemap_db: Path):
    """Build a Flask app with /treatments wired against the synthetic DB."""
    from flask import Flask

    from app.routes.treatments import build_treatments_blueprint

    # Seed a treatment row directly into the synthetic DB.
    from prep.db.builder import DbBuilder
    builder = DbBuilder(tiny_bikemap_db)
    builder.insert_treatments([(
        "neighborhood-greenway",
        "neighborhood-greenway",
        "ward-44",
        41.94, -87.67,
        None,
        "https://example.com/source",
        "Brief summary",
        "# Neighborhood greenway\n\nFull markdown body.",
    )])
    builder.close()

    app = Flask(__name__)
    app.register_blueprint(build_treatments_blueprint(tiny_bikemap_db))
    return app


def test_treatments_returns_treatment_by_slug(treatments_app) -> None:
    client = treatments_app.test_client()
    resp = client.get("/treatments/neighborhood-greenway")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["slug"] == "neighborhood-greenway"
    assert data["summary"] == "Brief summary"
    assert "markdown" in data
    assert "# Neighborhood greenway" in data["markdown"]


def test_treatments_404_for_unknown_slug(treatments_app) -> None:
    client = treatments_app.test_client()
    resp = client.get("/treatments/does-not-exist")
    assert resp.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd chicago-bike-advocacy-map && .venv/bin/pytest tests/app/test_routes_treatments.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.routes.treatments'`

- [ ] **Step 3: Implement treatments.py**

Create `app/routes/treatments.py`:

```python
"""GET /treatments/:slug — serve markdown library entries.

Loaded once at blueprint construction; ~5 entries in current data.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from flask import Blueprint, jsonify


def _load_treatments(db_path: Path) -> dict[str, dict]:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT slug, type, ward, location_lat, location_lng, "
        "photo_path, source_url, summary, body_md FROM treatments"
    ).fetchall()
    con.close()
    return {
        r["slug"]: {
            "slug": r["slug"],
            "type": r["type"],
            "ward": r["ward"],
            "location": (
                {"lat": r["location_lat"], "lon": r["location_lng"]}
                if r["location_lat"] is not None else None
            ),
            "photo_path": r["photo_path"],
            "source_url": r["source_url"],
            "summary": r["summary"],
            "markdown": r["body_md"],
        }
        for r in rows
    }


def build_treatments_blueprint(db_path: Path) -> Blueprint:
    """Construct the treatments blueprint, eagerly loading the table into memory."""
    treatments = _load_treatments(db_path)
    bp = Blueprint("treatments", __name__)

    @bp.get("/treatments/<slug>")
    def get_treatment(slug: str):
        t = treatments.get(slug)
        if t is None:
            return jsonify({"error": "treatment not found"}), 404
        return jsonify(t)

    return bp
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd chicago-bike-advocacy-map && .venv/bin/pytest tests/app/test_routes_treatments.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add chicago-bike-advocacy-map/app/routes/treatments.py chicago-bike-advocacy-map/tests/app/test_routes_treatments.py
git commit -m "feat(app): /treatments/:slug route"
```

---

## Task 9: `/pois` route (`app/routes/pois.py`)

**Files:**
- Create: `chicago-bike-advocacy-map/app/routes/pois.py`
- Test: `chicago-bike-advocacy-map/tests/app/test_routes_pois.py`

**Spec ref:** §3.6, §3.8 (POST body for coordinates, never URL params).

**Design notes:**
- Coordinates are accepted in POST JSON body (spec §3.8: never in query string for privacy).
- Returns `{name, address, category, lat, lon}` for the nearest POI in the requested category, OR a list of N nearest if `limit` is provided.

- [ ] **Step 1: Write the failing tests**

Create `tests/app/test_routes_pois.py`:

```python
"""Tests for /pois route."""
from pathlib import Path

import pytest


@pytest.fixture
def pois_app(tiny_bikemap_db_with_pois: Path):
    from flask import Flask

    from app.core.poi_picker import load_pois
    from app.routes.pois import build_pois_blueprint

    app = Flask(__name__)
    app.register_blueprint(build_pois_blueprint(load_pois(tiny_bikemap_db_with_pois)))
    return app


def test_pois_post_returns_nearest_in_category(pois_app) -> None:
    client = pois_app.test_client()
    resp = client.post("/pois", json={
        "near": {"lat": 41.940, "lon": -87.670},
        "category": "school",
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["name"] == "Test Elementary"
    assert data["category"] == "school"
    assert "lat" in data and "lon" in data


def test_pois_post_returns_404_for_unknown_category(pois_app) -> None:
    client = pois_app.test_client()
    resp = client.post("/pois", json={
        "near": {"lat": 41.940, "lon": -87.670},
        "category": "nonexistent",
    })
    assert resp.status_code == 404


def test_pois_post_validates_payload(pois_app) -> None:
    client = pois_app.test_client()
    resp = client.post("/pois", json={"category": "school"})  # missing 'near'
    assert resp.status_code == 400


def test_pois_get_method_disallowed(pois_app) -> None:
    """Spec §3.8: coordinates never in URL query string. GET must be 405."""
    client = pois_app.test_client()
    resp = client.get("/pois?lat=41.94&lon=-87.67&category=school")
    assert resp.status_code == 405
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd chicago-bike-advocacy-map && .venv/bin/pytest tests/app/test_routes_pois.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.routes.pois'`

- [ ] **Step 3: Implement pois.py**

Create `app/routes/pois.py`:

```python
"""POST /pois — find the nearest POI of a given category.

Coordinates accepted only via POST JSON body (spec §3.8).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from app.core.poi_picker import Poi, nearest_poi


def build_pois_blueprint(pois_by_category: dict[str, list[Poi]]) -> Blueprint:
    bp = Blueprint("pois", __name__)

    @bp.post("/pois")
    def find_poi():
        body = request.get_json(silent=True) or {}
        near = body.get("near") or {}
        cat = body.get("category")
        try:
            lat = float(near["lat"])
            lon = float(near["lon"])
        except (KeyError, TypeError, ValueError):
            return jsonify({"error": "invalid 'near': expected {lat, lon}"}), 400
        if not isinstance(cat, str):
            return jsonify({"error": "missing 'category'"}), 400
        pois = pois_by_category.get(cat)
        if not pois:
            return jsonify({"error": f"no POIs in category '{cat}'"}), 404
        p = nearest_poi(pois, lat, lon)
        if p is None:
            return jsonify({"error": "no POI found"}), 404
        return jsonify({
            "poi_id": p.poi_id,
            "name": p.name,
            "address": p.address,
            "category": p.category,
            "source": p.source,
            "lat": p.lat,
            "lon": p.lon,
        })

    return bp
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd chicago-bike-advocacy-map && .venv/bin/pytest tests/app/test_routes_pois.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add chicago-bike-advocacy-map/app/routes/pois.py chicago-bike-advocacy-map/tests/app/test_routes_pois.py
git commit -m "feat(app): POST /pois nearest-by-category route"
```

---

## Task 10: `/routes` route (`app/routes/routing.py`)

**Files:**
- Create: `chicago-bike-advocacy-map/app/routes/routing.py`
- Test: `chicago-bike-advocacy-map/tests/app/test_routes_routing.py`

**Spec ref:** §3.8 (POST body), §4.1 (returns both fast and safe routes).

**Design notes:**
- Returns both fast and safe routes for a single home→destination pair, with their geometries (vertex coords as a polyline) and metadata.
- **Snap-distance threshold (Fix 8):** if the user's home or dest snaps to a vertex more than 5 km away, return 400 — this means the input is outside Cook County and our routing graph can't help. `nearest_vertex` returns `(idx, distance_m)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/app/test_routes_routing.py`:

```python
"""Tests for /routes route."""
from pathlib import Path

import pytest


@pytest.fixture
def routes_app(tiny_bikemap_db: Path):
    from flask import Flask

    from app.core.graph import load_graph
    from app.routes.routing import build_routes_blueprint

    app = Flask(__name__)
    app.register_blueprint(build_routes_blueprint(load_graph(tiny_bikemap_db)))
    return app


def test_routes_returns_fast_and_safe_for_any_tier(routes_app) -> None:
    client = routes_app.test_client()
    resp = client.post("/routes", json={
        "home": {"lat": 41.940, "lon": -87.680},  # near v100
        "dest": {"lat": 41.940, "lon": -87.670},  # near v400
        "tier": "any",
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert "fast" in data
    assert "safe" in data
    assert data["fast"]["length_m"] > 0
    assert isinstance(data["fast"]["polyline"], list)
    assert isinstance(data["safe"]["polyline"], list)
    assert data["safe"]["is_fallback"] is False


def test_routes_flags_fallback_at_kid_tier_when_blocked(routes_app) -> None:
    client = routes_app.test_client()
    # v100 → v500 at kid tier — known blocked (LTS-3 chokepoint).
    resp = client.post("/routes", json={
        "home": {"lat": 41.940, "lon": -87.680},
        "dest": {"lat": 41.935, "lon": -87.675},
        "tier": "kid",
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["safe"]["is_fallback"] is True


def test_routes_400_on_invalid_tier(routes_app) -> None:
    client = routes_app.test_client()
    resp = client.post("/routes", json={
        "home": {"lat": 41.94, "lon": -87.68},
        "dest": {"lat": 41.94, "lon": -87.67},
        "tier": "BOGUS",
    })
    assert resp.status_code == 400


def test_routes_400_when_home_far_outside_graph_extent(routes_app) -> None:
    """Fix 8: home in Wisconsin (>5km from any synthetic vertex) → 400."""
    client = routes_app.test_client()
    resp = client.post("/routes", json={
        "home": {"lat": 43.0, "lon": -89.0},   # Madison, WI
        "dest": {"lat": 41.94, "lon": -87.67},
        "tier": "any",
    })
    assert resp.status_code == 400
    assert "outside" in resp.get_json()["error"].lower() or \
           "too far" in resp.get_json()["error"].lower()


def test_routes_get_method_disallowed(routes_app) -> None:
    client = routes_app.test_client()
    resp = client.get("/routes?home_lat=41.94&home_lon=-87.68&dest_lat=41.94&dest_lon=-87.67&tier=any")
    assert resp.status_code == 405
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd chicago-bike-advocacy-map && .venv/bin/pytest tests/app/test_routes_routing.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.routes.routing'`

- [ ] **Step 3: Implement routing.py**

Create `app/routes/routing.py`:

```python
"""POST /routes — fast + safe routes for one home→destination pair.

Coordinates accepted only via POST JSON body (spec §3.8).
Inputs that snap to a vertex >5 km away are rejected with 400 (Fix 8).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from app.core.graph import GraphSnapshot, nearest_vertex
from app.core.routing import Route, compute_fast_route, compute_safe_route
from app.core.weights import TIERS

# Reject inputs whose nearest vertex is more than this far away (likely
# outside Cook County). 5 km is generous — a legitimate Chicago address
# should snap to within a few hundred metres.
MAX_SNAP_DISTANCE_M = 5000.0


def _route_to_payload(snap: GraphSnapshot, r: Route | None) -> dict | None:
    if r is None:
        return None
    polyline = [
        {"lat": snap.vertex_coords_wgs84[v][0], "lon": snap.vertex_coords_wgs84[v][1]}
        for v in r.vertex_path
    ]
    return {
        "polyline": polyline,
        "length_m": r.length_m,
        "is_fallback": r.is_fallback,
        "lts_distribution": r.lts_distribution,
    }


def build_routes_blueprint(snap: GraphSnapshot) -> Blueprint:
    bp = Blueprint("routes", __name__)

    @bp.post("/routes")
    def routes():
        body = request.get_json(silent=True) or {}
        home = body.get("home") or {}
        dest = body.get("dest") or {}
        tier = body.get("tier")
        try:
            h_lat = float(home["lat"]); h_lon = float(home["lon"])
            d_lat = float(dest["lat"]); d_lon = float(dest["lon"])
        except (KeyError, TypeError, ValueError):
            return jsonify({"error": "invalid 'home' or 'dest'"}), 400
        if tier not in TIERS:
            return jsonify({"error": f"invalid tier '{tier}'"}), 400

        src_v, src_dist = nearest_vertex(snap, h_lat, h_lon)
        dst_v, dst_dist = nearest_vertex(snap, d_lat, d_lon)
        if src_dist > MAX_SNAP_DISTANCE_M or dst_dist > MAX_SNAP_DISTANCE_M:
            return jsonify({
                "error": "home or dest is outside the graph's extent (too far from any intersection)",
            }), 400

        fast = compute_fast_route(snap, src_v, dst_v)
        safe = compute_safe_route(snap, src_v, dst_v, tier)

        return jsonify({
            "fast": _route_to_payload(snap, fast),
            "safe": _route_to_payload(snap, safe),
        })

    return bp
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd chicago-bike-advocacy-map && .venv/bin/pytest tests/app/test_routes_routing.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add chicago-bike-advocacy-map/app/routes/routing.py chicago-bike-advocacy-map/tests/app/test_routes_routing.py
git commit -m "feat(app): POST /routes returns fast + safe per tier"
```

---

## Task 11: `/geocode` route (`app/routes/geocode.py`)

**Files:**
- Create: `chicago-bike-advocacy-map/app/routes/geocode.py`
- Test: `chicago-bike-advocacy-map/tests/app/test_routes_geocode.py`

**Spec ref:** §3.7 (Nominatim with self-throttling), §3.8 (server proxies; no address logging).

**Design notes:**
- This is a simple proxy: client sends an address string in POST body; server forwards to Nominatim with the configured user-agent (env var `NOMINATIM_USER_AGENT`); returns the first result's lat/lon + display_name.
- Self-throttle: a minimum 1.1s gap between Nominatim requests (their TOS).
- Logging: address is NOT written to access logs.
- Tests use `requests-mock` (already installed via geopandas's deps? Check; if not, use `unittest.mock.patch`).

- [ ] **Step 1: Write the failing tests**

Create `tests/app/test_routes_geocode.py`:

```python
"""Tests for /geocode proxy."""
from unittest.mock import patch


def _make_app():
    from flask import Flask

    from app.routes.geocode import build_geocode_blueprint

    app = Flask(__name__)
    app.register_blueprint(build_geocode_blueprint(user_agent="test/1.0"))
    return app


def test_geocode_proxies_to_nominatim_and_returns_first_result() -> None:
    app = _make_app()
    fake_response = [{
        "display_name": "1234 W Foster Ave, Chicago, IL, USA",
        "lat": "41.9755",
        "lon": "-87.6890",
    }]
    with patch("app.routes.geocode._fetch_nominatim", return_value=fake_response):
        client = app.test_client()
        resp = client.post("/geocode", json={"address": "1234 W Foster Ave"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["lat"] == 41.9755
        assert data["lon"] == -87.6890
        assert "Foster" in data["display_name"]


def test_geocode_returns_404_when_no_results() -> None:
    app = _make_app()
    with patch("app.routes.geocode._fetch_nominatim", return_value=[]):
        client = app.test_client()
        resp = client.post("/geocode", json={"address": "blank"})
        assert resp.status_code == 404


def test_geocode_400_on_missing_address() -> None:
    app = _make_app()
    client = app.test_client()
    resp = client.post("/geocode", json={})
    assert resp.status_code == 400
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd chicago-bike-advocacy-map && .venv/bin/pytest tests/app/test_routes_geocode.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.routes.geocode'`

- [ ] **Step 3: Implement geocode.py**

Create `app/routes/geocode.py`:

```python
"""POST /geocode — proxy address strings to Nominatim with self-throttling.

Address is sent to Nominatim (necessarily) but never written to our logs.
"""
from __future__ import annotations

import threading
import time
from typing import Any

import requests
from flask import Blueprint, jsonify, request

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
MIN_INTERVAL_S = 1.1  # Nominatim TOS

_throttle_lock = threading.Lock()
_last_request_at = [0.0]


def _fetch_nominatim(address: str, user_agent: str) -> list[dict[str, Any]]:
    """Throttled Nominatim search. Internal seam; tests patch this."""
    with _throttle_lock:
        gap = time.monotonic() - _last_request_at[0]
        if gap < MIN_INTERVAL_S:
            time.sleep(MIN_INTERVAL_S - gap)
        _last_request_at[0] = time.monotonic()
    resp = requests.get(
        NOMINATIM_URL,
        params={"q": address, "format": "json", "limit": 1, "countrycodes": "us"},
        headers={"User-Agent": user_agent},
        timeout=8,
    )
    resp.raise_for_status()
    return resp.json()


def build_geocode_blueprint(user_agent: str) -> Blueprint:
    bp = Blueprint("geocode", __name__)

    @bp.post("/geocode")
    def geocode():
        body = request.get_json(silent=True) or {}
        address = body.get("address")
        if not isinstance(address, str) or not address.strip():
            return jsonify({"error": "missing 'address'"}), 400
        try:
            results = _fetch_nominatim(address, user_agent)
        except requests.RequestException as e:
            return jsonify({"error": f"geocoder error: {e.__class__.__name__}"}), 502
        if not results:
            return jsonify({"error": "no results"}), 404
        first = results[0]
        return jsonify({
            "display_name": first.get("display_name"),
            "lat": float(first["lat"]),
            "lon": float(first["lon"]),
        })

    return bp
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd chicago-bike-advocacy-map && .venv/bin/pytest tests/app/test_routes_geocode.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add chicago-bike-advocacy-map/app/routes/geocode.py chicago-bike-advocacy-map/tests/app/test_routes_geocode.py
git commit -m "feat(app): POST /geocode Nominatim proxy with throttling"
```

---

## Task 12: `/gap-analysis` route + async polling (`app/routes/gap_analysis.py`)

**Files:**
- Create: `chicago-bike-advocacy-map/app/routes/gap_analysis.py`
- Test: `chicago-bike-advocacy-map/tests/app/test_routes_gap_analysis.py`

**Spec ref:** §3.5 (cache + async polling), §4.5.

**Design notes:**
- `POST /gap-analysis` body: `{home: {lat, lon}, dest: {lat, lon}, tier}`.
- Cache hit → return result with `{status: "ready", result: ...}`.
- Cache miss → submit job to a `concurrent.futures.ThreadPoolExecutor` (max_workers=3 per spec §6.4 #8 cap), return `{status: "running", job_id: ...}`.
- `GET /gap-analysis/status?job=<id>`: returns `{status: "running" | "ready" | "error", result?: ...}` based on the future state.
- On completion, the worker thread writes to cache.db.
- Job state: in-process dict `{job_id: Future}` plus a TTL-based cleanup (drop futures older than 10 minutes when polled). Process-local — for v1 single-worker gunicorn this is fine.
- Like `/routes`, snap-distance > 5 km on either coordinate → 400 (Fix 8).
- `analyze_gap` no longer takes `db_path` (Fix 3) — `bikemap_db` is still passed to the blueprint constructor for the cache fingerprint check, but the gap algorithm itself runs entirely from the GraphSnapshot's in-memory arrays.

- [ ] **Step 1: Write the failing tests**

Create `tests/app/test_routes_gap_analysis.py`:

```python
"""Tests for /gap-analysis + /gap-analysis/status."""
import time
from pathlib import Path

import pytest


@pytest.fixture
def gap_app(tmp_path: Path, tiny_bikemap_db: Path):
    from flask import Flask

    from app.core.cache import bikemap_fingerprint, init_cache_db
    from app.core.graph import load_graph
    from app.routes.gap_analysis import build_gap_analysis_blueprint

    cache_path = tmp_path / "cache.db"
    init_cache_db(cache_path, fingerprint=bikemap_fingerprint(tiny_bikemap_db))

    app = Flask(__name__)
    app.register_blueprint(build_gap_analysis_blueprint(
        snap=load_graph(tiny_bikemap_db),
        cache_db=cache_path,
    ))
    return app


def _wait_until_ready(client, job_id: str, timeout_s: float = 5.0) -> dict:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        resp = client.get(f"/gap-analysis/status?job={job_id}")
        data = resp.get_json()
        if data["status"] in ("ready", "error"):
            return data
        time.sleep(0.05)
    raise TimeoutError("gap-analysis job did not complete")


def test_gap_analysis_first_call_returns_running_then_ready(gap_app) -> None:
    client = gap_app.test_client()
    resp = client.post("/gap-analysis", json={
        "home": {"lat": 41.940, "lon": -87.680},
        "dest": {"lat": 41.935, "lon": -87.675},
        "tier": "any",
    })
    assert resp.status_code == 202
    data = resp.get_json()
    assert data["status"] == "running"
    assert "job_id" in data
    final = _wait_until_ready(client, data["job_id"])
    assert final["status"] == "ready"
    assert "result" in final


def test_gap_analysis_cache_hit_returns_ready_immediately(gap_app) -> None:
    client = gap_app.test_client()
    body = {
        "home": {"lat": 41.940, "lon": -87.680},
        "dest": {"lat": 41.935, "lon": -87.675},
        "tier": "any",
    }
    # Prime cache
    first = client.post("/gap-analysis", json=body)
    job_id = first.get_json()["job_id"]
    _wait_until_ready(client, job_id)
    # Second call should be cache hit
    second = client.post("/gap-analysis", json=body)
    assert second.status_code == 200
    data = second.get_json()
    assert data["status"] == "ready"
    assert "result" in data


def test_gap_analysis_status_404_for_unknown_job(gap_app) -> None:
    client = gap_app.test_client()
    resp = client.get("/gap-analysis/status?job=nonsense")
    assert resp.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd chicago-bike-advocacy-map && .venv/bin/pytest tests/app/test_routes_gap_analysis.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.routes.gap_analysis'`

- [ ] **Step 3: Implement gap_analysis.py**

Create `app/routes/gap_analysis.py`:

```python
"""POST /gap-analysis + GET /gap-analysis/status — async gap computation.

Cache hit returns {status: ready, result} immediately. Cache miss submits
a job to a 3-worker thread pool, returns 202 with {status: running, job_id}.
Client polls /gap-analysis/status?job= every 1.5s (per spec §3.5).
"""
from __future__ import annotations

import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from flask import Blueprint, jsonify, request

from app.core.cache import cache_key, get_cached_gap, put_cached_gap
from app.core.gap_analysis import GapResult, analyze_gap
from app.core.graph import GraphSnapshot, nearest_vertex
from app.core.weights import TIERS

JOB_TTL_S = 600  # drop completed/failed futures after 10 minutes


def _serialize(result: GapResult, snap: GraphSnapshot) -> dict[str, Any]:
    """Make GapResult JSON-friendly. Includes route polylines (Fix B) so the
    frontend doesn't need a second /routes call to draw the routes."""
    def _route_dict(r) -> dict | None:
        if r is None:
            return None
        polyline = [
            {"lat": snap.vertex_coords_wgs84[v][0], "lon": snap.vertex_coords_wgs84[v][1]}
            for v in r.vertex_path
        ]
        return {
            "polyline": polyline,
            "edge_count": len(r.edge_path),
            "length_m": r.length_m,
            "is_fallback": r.is_fallback,
            "lts_distribution": r.lts_distribution,
        }

    def _cand_dict(c) -> dict:
        return asdict(c) if is_dataclass(c) else dict(c)

    return {
        "fast_route": _route_dict(result.fast_route),
        "safe_route": _route_dict(result.safe_route),
        "safe_route_is_fallback": result.safe_route_is_fallback,
        "headline": _cand_dict(result.headline) if result.headline else None,
        "supporting": [_cand_dict(c) for c in result.supporting],
        "corridor": [_cand_dict(c) for c in result.corridor],
    }


def build_gap_analysis_blueprint(
    snap: GraphSnapshot, cache_db: Path,
) -> Blueprint:
    bp = Blueprint("gap_analysis", __name__)
    executor = ThreadPoolExecutor(max_workers=3)
    jobs: dict[str, tuple[Future, float]] = {}  # job_id -> (future, submitted_at)

    def _gc_jobs() -> None:
        """Drop stale completed/failed futures."""
        now = time.time()
        for jid in list(jobs.keys()):
            fut, submitted = jobs[jid]
            if fut.done() and (now - submitted) > JOB_TTL_S:
                jobs.pop(jid, None)

    def _compute(home: tuple[float, float], dest: tuple[float, float], tier: str) -> dict:
        src_v, _ = nearest_vertex(snap, *home)
        dst_v, _ = nearest_vertex(snap, *dest)
        result = analyze_gap(snap, src_v, dst_v, tier)
        payload = _serialize(result, snap)
        put_cached_gap(cache_db, cache_key(home, dest, tier), payload)
        return payload

    @bp.post("/gap-analysis")
    def submit():
        body = request.get_json(silent=True) or {}
        home = body.get("home") or {}
        dest = body.get("dest") or {}
        tier = body.get("tier")
        try:
            h = (float(home["lat"]), float(home["lon"]))
            d = (float(dest["lat"]), float(dest["lon"]))
        except (KeyError, TypeError, ValueError):
            return jsonify({"error": "invalid 'home' or 'dest'"}), 400
        if tier not in TIERS:
            return jsonify({"error": f"invalid tier '{tier}'"}), 400

        # Snap-distance check (Fix 8).
        _, h_dist = nearest_vertex(snap, *h)
        _, d_dist = nearest_vertex(snap, *d)
        if h_dist > 5000.0 or d_dist > 5000.0:
            return jsonify({
                "error": "home or dest is outside the graph's extent",
            }), 400

        key = cache_key(h, d, tier)
        cached = get_cached_gap(cache_db, key)
        if cached is not None:
            return jsonify({"status": "ready", "result": cached}), 200

        _gc_jobs()
        job_id = uuid.uuid4().hex
        fut = executor.submit(_compute, h, d, tier)
        jobs[job_id] = (fut, time.time())
        return jsonify({"status": "running", "job_id": job_id}), 202

    @bp.get("/gap-analysis/status")
    def status():
        job_id = request.args.get("job")
        if not job_id or job_id not in jobs:
            return jsonify({"error": "unknown job"}), 404
        fut, _ = jobs[job_id]
        if not fut.done():
            return jsonify({"status": "running", "job_id": job_id}), 200
        try:
            result = fut.result()
        except Exception as e:  # noqa: BLE001
            return jsonify({"status": "error", "error": str(e)}), 500
        return jsonify({"status": "ready", "result": result}), 200

    return bp
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd chicago-bike-advocacy-map && .venv/bin/pytest tests/app/test_routes_gap_analysis.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add chicago-bike-advocacy-map/app/routes/gap_analysis.py chicago-bike-advocacy-map/tests/app/test_routes_gap_analysis.py
git commit -m "feat(app): /gap-analysis with thread-pool job runner + cache"
```

---

## Task 13: Flask app factory + `/health` + rate limiting (`app/main.py`)

**Files:**
- Create: `chicago-bike-advocacy-map/app/main.py`
- Test: `chicago-bike-advocacy-map/tests/app/test_main.py`

**Spec ref:** §3.10 (startup validation, single-worker Gunicorn `-w 1 --threads 4`, rate limiting 60 req/min via slowapi).

**Design notes:**
- `create_app(*, bikemap_db, cache_db, nominatim_user_agent)` returns a Flask app.
- **`/health` 503-during-load deviation (Fix 4):** spec §3.10 wants `/health` to return 503 while the graph is loading, then 200. `create_app` is synchronous — the WSGI app isn't bound to a port until graph load completes (~35s in production), so external callers see connection-refused, not 503. **This is functionally equivalent for Render's health-check semantics**: Render's `initialDelaySeconds: 120` (spec §5.6) covers the load window, and connection-refused looks the same as a 503 to the orchestrator (both = "not ready, retry"). We accept this deviation in v1; an async-startup variant (background graph-load thread + ready flag) is deferred to Plan 2C.
- Rate limiting via `slowapi` — 60 req/min per IP keyed off X-Forwarded-For. The `Limiter(default_limits=...)` API applies to all routes registered on the app afterward; the limiter is initialized BEFORE blueprints are registered. `@limiter.exempt` on `/health` is required so liveness probes aren't rate-limited.
- Startup validation: bikemap.db exists, schema_version compatible, streets row count ≥ 1 (or ≥ 10000 in production; test uses tiny DB so threshold is configurable via env).

- [ ] **Step 1: Write the failing tests**

Create `tests/app/test_main.py`:

```python
"""Tests for the Flask app factory."""
import os
from pathlib import Path


def test_create_app_returns_flask_app_with_blueprints(
    tiny_bikemap_db_with_pois: Path, tmp_path: Path,
) -> None:
    from flask import Flask

    from app.main import create_app

    cache_db = tmp_path / "cache.db"
    app = create_app(
        bikemap_db=tiny_bikemap_db_with_pois,
        cache_db=cache_db,
        nominatim_user_agent="test/1.0",
        min_streets=1,
    )
    assert isinstance(app, Flask)
    # Blueprints registered: routes, pois, treatments, geocode, gap_analysis.
    bp_names = {bp.name for bp in app.blueprints.values()}
    assert {"routes", "pois", "treatments", "geocode", "gap_analysis"}.issubset(bp_names)


def test_health_endpoint_returns_200_when_loaded(
    tiny_bikemap_db_with_pois: Path, tmp_path: Path,
) -> None:
    from app.main import create_app

    app = create_app(
        bikemap_db=tiny_bikemap_db_with_pois,
        cache_db=tmp_path / "cache.db",
        nominatim_user_agent="test/1.0",
        min_streets=1,
    )
    client = app.test_client()
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "ok"


def test_create_app_raises_when_bikemap_missing(tmp_path: Path) -> None:
    import pytest

    from app.main import create_app

    with pytest.raises(FileNotFoundError):
        create_app(
            bikemap_db=tmp_path / "missing.db",
            cache_db=tmp_path / "cache.db",
            nominatim_user_agent="test/1.0",
            min_streets=1,
        )


def test_create_app_raises_on_insufficient_streets(
    tiny_bikemap_db: Path, tmp_path: Path,
) -> None:
    """Startup validation: streets row count must meet min_streets threshold."""
    import pytest

    from app.main import create_app

    with pytest.raises(RuntimeError, match="streets"):
        create_app(
            bikemap_db=tiny_bikemap_db,
            cache_db=tmp_path / "cache.db",
            nominatim_user_agent="test/1.0",
            min_streets=10000,  # synthetic DB has only 5
        )


def test_create_app_raises_on_old_schema_version(tmp_path: Path) -> None:
    """Fix E: a bikemap.db with schema_version < MIN_SCHEMA_VERSION fails
    fast at startup rather than producing OperationalError at request time."""
    import pytest
    import sqlite3

    from app.main import create_app

    db_path = tmp_path / "old_schema.db"
    # Hand-build a stub DB with schema_version=1 (the pre-migration version).
    con = sqlite3.connect(db_path)
    con.executescript("""
        CREATE TABLE schema_meta (schema_version INTEGER NOT NULL,
                                  built_at TEXT NOT NULL,
                                  code_version TEXT);
        CREATE TABLE streets (osm_id INTEGER PRIMARY KEY, lts INTEGER NOT NULL);
        INSERT INTO schema_meta (schema_version, built_at, code_version)
            VALUES (1, '2025-01-01', 'old');
    """)
    con.commit()
    con.close()

    with pytest.raises(RuntimeError, match="schema_version"):
        create_app(
            bikemap_db=db_path,
            cache_db=tmp_path / "cache.db",
            nominatim_user_agent="test/1.0",
            min_streets=0,
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd chicago-bike-advocacy-map && .venv/bin/pytest tests/app/test_main.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.main'`

- [ ] **Step 3: Implement main.py**

Create `app/main.py`:

```python
"""Flask app factory.

Production entry: gunicorn -w 1 --threads 4 -b 0.0.0.0:$PORT app.main:app.
Local dev: `flask --app app.main run --no-reload` (avoid reloading the
30+s graph load on every code edit).

Env vars (read in __main__ block at the bottom):
  BIKEMAP_DB_PATH            default: data/bikemap.db
  CACHE_DB_PATH              default: data/cache.db
  NOMINATIM_USER_AGENT       default: chicago-bike-advocacy-map/1.0
  MIN_STREETS                default: 10000  (spec §3.10 startup validation)
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from flask import Flask, jsonify
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.cache import bikemap_fingerprint, init_cache_db
from app.core.graph import load_graph
from app.core.poi_picker import load_pois
from app.routes.gap_analysis import build_gap_analysis_blueprint
from app.routes.geocode import build_geocode_blueprint
from app.routes.pois import build_pois_blueprint
from app.routes.routing import build_routes_blueprint
from app.routes.treatments import build_treatments_blueprint


# Minimum bikemap.db schema version this code can read. Bump in lockstep
# with prep/db/builder.SCHEMA_VERSION and document the back-compat window
# (spec §3.11: code stays compatible with the last 2 schema versions).
MIN_SCHEMA_VERSION = 2

def _validate_bikemap(db_path: Path, min_streets: int) -> None:
    if not db_path.exists():
        raise FileNotFoundError(f"bikemap.db not found at {db_path}")
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        # Schema version check (Fix E). If the DB schema is older than
        # MIN_SCHEMA_VERSION, columns this code reads (e.g., streets.road_id)
        # may not exist — fail loudly at startup rather than at request time.
        sv_row = con.execute(
            "SELECT schema_version FROM schema_meta LIMIT 1"
        ).fetchone()
        if sv_row is None:
            raise RuntimeError("bikemap.db has no schema_meta row")
        schema_version = int(sv_row[0])
        if schema_version < MIN_SCHEMA_VERSION:
            raise RuntimeError(
                f"bikemap.db schema_version={schema_version} is older than "
                f"MIN_SCHEMA_VERSION={MIN_SCHEMA_VERSION}; rebuild the DB."
            )
        n = con.execute("SELECT COUNT(*) FROM streets").fetchone()[0]
    finally:
        con.close()
    if n < min_streets:
        raise RuntimeError(
            f"bikemap.db has {n} streets — below min_streets={min_streets}"
        )


def create_app(
    *,
    bikemap_db: Path,
    cache_db: Path,
    nominatim_user_agent: str,
    min_streets: int = 10000,
) -> Flask:
    _validate_bikemap(bikemap_db, min_streets)

    init_cache_db(cache_db, fingerprint=bikemap_fingerprint(bikemap_db))
    snap = load_graph(bikemap_db)
    pois_by_category = load_pois(bikemap_db)

    app = Flask(__name__)

    # Per-IP rate limiting (spec §3.10 — 60 req/min).
    limiter = Limiter(get_remote_address, app=app, default_limits=["60 per minute"])
    # Health endpoint is unlimited.
    app.register_blueprint(build_routes_blueprint(snap))
    app.register_blueprint(build_pois_blueprint(pois_by_category))
    app.register_blueprint(build_treatments_blueprint(bikemap_db))
    app.register_blueprint(build_geocode_blueprint(user_agent=nominatim_user_agent))
    app.register_blueprint(build_gap_analysis_blueprint(
        snap=snap, cache_db=cache_db,
    ))

    @app.get("/health")
    @limiter.exempt
    def health():
        return jsonify({"status": "ok", "streets": snap.g.ecount() // 2,
                        "vertices": snap.g.vcount()})

    return app


def _make_default_app() -> Flask:
    return create_app(
        bikemap_db=Path(os.environ.get("BIKEMAP_DB_PATH", "data/bikemap.db")),
        cache_db=Path(os.environ.get("CACHE_DB_PATH", "data/cache.db")),
        nominatim_user_agent=os.environ.get(
            "NOMINATIM_USER_AGENT", "chicago-bike-advocacy-map/1.0",
        ),
        min_streets=int(os.environ.get("MIN_STREETS", "10000")),
    )


# WSGI entry point for gunicorn.
# Lazy: only build the app when imported by gunicorn, not at module-import time
# during testing.
if os.environ.get("APP_BOOTSTRAP", "0") == "1":
    app = _make_default_app()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd chicago-bike-advocacy-map && .venv/bin/pytest tests/app/test_main.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add chicago-bike-advocacy-map/app/main.py chicago-bike-advocacy-map/tests/app/test_main.py
git commit -m "feat(app): Flask factory wires blueprints + /health + rate limit"
```

---

## Task 14: End-to-end smoke + memory benchmark against real bikemap.db

**Files:**
- Create: `chicago-bike-advocacy-map/tests/app/test_smoke_real_db.py`

**Spec ref:** §6.4 #4 (10 hand-tested addresses), #8 (perf budget), #9 (memory < 480 MB).

**Design notes:**
- This is a slow test (loads 69 MB DB, ~30s startup). Mark with `@pytest.mark.slow` and skip by default; run via `.venv/bin/pytest -m slow tests/app/test_smoke_real_db.py`.
- Verifies: app boots against real DB; /routes returns sane Lake View → Loop responses; resident memory under 480 MB.
- This test is the developer's "is it actually working" lever before Plan 2B.

- [ ] **Step 1: Configure pytest marker**

Add to `pyproject.toml` under `[tool.pytest.ini_options]`:

```toml
markers = [
    "slow: tests that load the real bikemap.db (~30s); skipped by default",
]
addopts = "-v --tb=short -m 'not slow'"
```

(If `addopts` already exists with `-v --tb=short`, replace it with the line above.)

- [ ] **Step 2: Write the smoke test**

Create `tests/app/test_smoke_real_db.py`:

```python
"""End-to-end smoke test against the real Chicago bikemap.db.

Skipped by default (slow). Run explicitly:
    .venv/bin/pytest -m slow tests/app/test_smoke_real_db.py
"""
import os
from pathlib import Path

import psutil
import pytest

REAL_DB = Path(__file__).parent.parent.parent / "data" / "bikemap.db"


@pytest.mark.slow
@pytest.mark.skipif(not REAL_DB.exists(), reason="real bikemap.db missing")
def test_routes_and_memory_against_real_db(tmp_path: Path) -> None:
    from app.main import create_app

    p = psutil.Process()
    pre_mb = p.memory_info().rss / 1024 / 1024

    app = create_app(
        bikemap_db=REAL_DB,
        cache_db=tmp_path / "cache.db",
        nominatim_user_agent="smoke-test/1.0",
        min_streets=10000,
    )
    post_mb = p.memory_info().rss / 1024 / 1024

    # Spec §6.4 #9: resident memory < 480 MB.
    assert post_mb < 480, f"memory budget exceeded: {post_mb:.0f} MB"

    client = app.test_client()
    # Lake View → Loop, 'any' tier.
    resp = client.post("/routes", json={
        "home": {"lat": 41.9398, "lon": -87.6685},
        "dest": {"lat": 41.8819, "lon": -87.6278},
        "tier": "any",
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["fast"] is not None
    assert data["safe"] is not None
    fast_mi = data["fast"]["length_m"] / 1609.34
    safe_mi = data["safe"]["length_m"] / 1609.34
    # Crow-flies is ~4.5 mi; reasonable routes are 4.5-7 mi.
    assert 4.0 < fast_mi < 8.0, f"fast={fast_mi:.2f} mi outside expected range"
    assert 4.0 < safe_mi < 12.0, f"safe={safe_mi:.2f} mi outside expected range"

    # Health endpoint reports vertex/edge counts.
    h = client.get("/health").get_json()
    assert h["status"] == "ok"
    assert h["streets"] >= 100000   # Chicago has ~350k segments
    assert h["vertices"] >= 100000
```

- [ ] **Step 3: Run the smoke test (slow)**

Run: `cd chicago-bike-advocacy-map && .venv/bin/pytest -m slow tests/app/test_smoke_real_db.py -v`
Expected: 1 passed (~40s)

If memory exceeds 480 MB or routes look wrong, halt and investigate before continuing.

- [ ] **Step 4: Run the FULL fast test suite once more to confirm nothing else broke**

Run: `cd chicago-bike-advocacy-map && .venv/bin/pytest`
Expected: all fast tests pass; slow tests skipped.

- [ ] **Step 5: Run ruff + mypy**

Run: `cd chicago-bike-advocacy-map && .venv/bin/ruff check app/ tests/app/ && .venv/bin/mypy app/`
Expected: `All checks passed!` and `Success: no issues found`.

- [ ] **Step 6: Commit**

```bash
git add chicago-bike-advocacy-map/tests/app/test_smoke_real_db.py chicago-bike-advocacy-map/pyproject.toml
git commit -m "test(app): end-to-end smoke + memory benchmark against real bikemap.db"
```

---

## Done

After Task 14, Plan 2A is complete. The web service:

- Loads the real Chicago bikemap.db at startup (~34s, ~300 MB resident).
- Serves `/health`, `/geocode`, `/routes`, `/pois`, `/treatments/:slug`, `/gap-analysis`, `/gap-analysis/status`.
- Routes are correct for all three tiers across cross-town queries.
- Gap analysis runs synchronously per cache-miss request, returns within spec performance window for cross-town pairs.
- Cache survives restarts and resets when bikemap.db fingerprint changes.
- Rate limited at 60 req/min per IP.
- Memory budget verified under 480 MB.

**What's still needed before launch (Plan 2B + Plan 2C):**

- Frontend (Plan 2B): MapLibre + permalink + overview/drill-down views.
- Render deploy + Dockerfile + `make upload-db` (Plan 2C).
- Optional: pickled-igraph artifact for <10s startup if 34s ever becomes a problem.
- Async LRU eviction worker (currently synchronous after writes — fine until cache nears 500 MB in production).
- A* over Dijkstra if launch criterion §6.4 #8 turns out to fail under realistic load.
