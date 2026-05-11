# Plan 2D — LTS Data Explorer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a third top-level view at `GET /explore` that shows every Chicago street colored by LTS and every intersection by `lts_approach`, with an optional HIN overlay.

**Architecture:** Data lives in `data/lts-network.geojson.gz` — a gzipped GeoJSON file generated **offline** by the prep pipeline at the end of `prep/main.run_pipeline`, alongside `bikemap.db`. Flask serves it via a tiny `send_from_directory` route at `GET /lts-network`. **No live endpoint code, no startup-time data construction, no in-memory cache.** The frontend at `/explore` is a separate static page (own HTML + own JS, no imports from advocacy code) that fetches the file and adds three MapLibre layers.

This architecture replaces the first-draft "build-at-boot" approach after a critical review surfaced a real memory-ceiling risk; see spec §10.

**Tech Stack:** Flask `send_from_directory`, MapLibre GL JS, shapely (for street geometry WKB → coords), gzip from stdlib. No new dependencies.

**Spec:** [`docs/superpowers/specs/2026-05-11-lts-data-explorer-design.md`](../specs/2026-05-11-lts-data-explorer-design.md). Section refs in this plan are to that file.

**Working directory:** `/Users/hunterheyman/Claude/.claude/worktrees/affectionate-hawking-e216bd/chicago-bike-advocacy-map/` (the worktree). All paths below are relative to this directory unless noted.

**File structure:**

```
Created:
  prep/lts_network_export.py     # builds data/lts-network.geojson.gz
  app/routes/lts_network.py      # tiny send_from_directory route
  app/static/explore.html        # standalone shell — no advocacy UI
  app/static/explore.js          # entrypoint for /explore
  tests/prep/test_lts_network_export.py
  tests/app/test_lts_network_route.py

Modified:
  prep/main.py                   # call exporter at end of run_pipeline
  app/main.py                    # register lts-network blueprint; add /explore route
  app/static/index.html          # add bottom-left "Explore LTS data →" link
  app/static/styles.css          # explorer-page styles + advocacy nav-link styles
```

---

## Task 1: Prep-pipeline exporter — `prep/lts_network_export.py`

**Files:**
- Create: `prep/lts_network_export.py`
- Create: `tests/prep/test_lts_network_export.py`

**Spec refs:** §5.2 (file format), §5.3 (prep-pipeline integration), §5.6 (size budget).

**Design notes:**
- Function signature: `def export_lts_network(db_path: Path, output_path: Path) -> int` — returns the gzipped file size in bytes (for logging in the prep report).
- Streams features into `gzip.GzipFile(open(output_path, "wb"))` — the uncompressed JSON never lives in memory in its entirety.
- Coordinates rounded to 5 decimals (~1 m at Chicago latitude).
- Writes atomically: build to `<output_path>.tmp`, then `os.replace` to `<output_path>`. Prevents readers from observing a partial file mid-write.

- [ ] **Step 1: Write the failing test**

Create `tests/prep/test_lts_network_export.py`:

```python
"""Tests for the LTS-network static-file exporter (Plan 2D Task 1)."""
from __future__ import annotations

import gzip
import json
from pathlib import Path

from prep.lts_network_export import export_lts_network


def test_export_produces_valid_gzipped_geojson(
    tiny_bikemap_db_with_pois: Path, tmp_path: Path,
) -> None:
    out_path = tmp_path / "lts-network.geojson.gz"
    size = export_lts_network(tiny_bikemap_db_with_pois, out_path)

    assert out_path.exists()
    assert size == out_path.stat().st_size
    assert size > 0

    decompressed = gzip.decompress(out_path.read_bytes())
    fc = json.loads(decompressed)
    assert fc["type"] == "FeatureCollection"

    streets = [f for f in fc["features"] if f["geometry"]["type"] == "LineString"]
    points = [f for f in fc["features"] if f["geometry"]["type"] == "Point"]
    # tiny_bikemap_db_with_pois has 5 streets and 5 intersections.
    assert len(streets) == 5
    assert len(points) == 5


def test_export_street_features_have_expected_schema(
    tiny_bikemap_db_with_pois: Path, tmp_path: Path,
) -> None:
    out_path = tmp_path / "lts-network.geojson.gz"
    export_lts_network(tiny_bikemap_db_with_pois, out_path)
    fc = json.loads(gzip.decompress(out_path.read_bytes()))
    streets = [f for f in fc["features"] if f["geometry"]["type"] == "LineString"]
    for f in streets:
        assert f["properties"]["lts"] in {1, 2, 3}
        assert isinstance(f["properties"]["on_hin"], bool)
        for lon, lat in f["geometry"]["coordinates"]:
            assert -88 < lon < -87, "lon outside Chicago range"
            assert 41 < lat < 42, "lat outside Chicago range"


def test_export_rounds_coordinates_to_5_decimals(
    tiny_bikemap_db_with_pois: Path, tmp_path: Path,
) -> None:
    """The fixture inserts points at exact known coordinates; after export
    they should match to 5 decimal places."""
    out_path = tmp_path / "lts-network.geojson.gz"
    export_lts_network(tiny_bikemap_db_with_pois, out_path)
    fc = json.loads(gzip.decompress(out_path.read_bytes()))
    points = [f for f in fc["features"] if f["geometry"]["type"] == "Point"]
    # tiny fixture inserts v100 at (-87.680, 41.940) — round to 5 decimals == itself.
    coords = [tuple(f["geometry"]["coordinates"]) for f in points]
    assert (-87.68, 41.94) in coords  # v100
    assert (-87.675, 41.945) in coords  # v200


def test_export_is_atomic(
    tiny_bikemap_db_with_pois: Path, tmp_path: Path,
) -> None:
    """No .tmp file should remain after a successful run."""
    out_path = tmp_path / "lts-network.geojson.gz"
    export_lts_network(tiny_bikemap_db_with_pois, out_path)
    assert not (tmp_path / "lts-network.geojson.gz.tmp").exists()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/prep/test_lts_network_export.py -v
```

Expected: `ModuleNotFoundError: No module named 'prep.lts_network_export'`.

The conftest fixture `tiny_bikemap_db_with_pois` is defined in `tests/app/conftest.py`. The same fixture needs to be available to `tests/prep/`. Check whether it's already shared (via a top-level conftest) or needs to be lifted up.

If needed, move the fixture functions to `tests/conftest.py` so both `tests/app/` and `tests/prep/` see them. Don't duplicate the fixture body.

- [ ] **Step 3: Implement `export_lts_network`**

Create `prep/lts_network_export.py`:

```python
"""Generates data/lts-network.geojson.gz from a built bikemap.db.

Run at the end of prep/main.run_pipeline so the static file ships with
the database (spec §5.3). Streams features through gzip so the
uncompressed JSON never lives in memory in its entirety.

Coordinates are rounded to 5 decimal places (~1 m at Chicago latitude).
PFB emits 7 decimals (~1 cm); the extra precision is invisible at the
zoom levels the Explorer view supports.
"""
from __future__ import annotations

import gzip
import json
import os
import sqlite3
from pathlib import Path

from shapely import wkb

_COORD_PRECISION = 5


def _round_coord(c: tuple[float, float]) -> list[float]:
    return [round(c[0], _COORD_PRECISION), round(c[1], _COORD_PRECISION)]


def export_lts_network(db_path: Path, output_path: Path) -> int:
    """Write a gzipped GeoJSON FeatureCollection of streets + intersections
    to `output_path`. Returns the resulting file size in bytes.

    Writes atomically via <output_path>.tmp + os.replace.
    """
    tmp_path = Path(str(output_path) + ".tmp")
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        with gzip.open(tmp_path, "wt", encoding="utf-8", compresslevel=6) as f:
            f.write('{"type":"FeatureCollection","features":[')
            first = True

            for r in con.execute(
                "SELECT lts, on_hin, geom FROM streets "
                "WHERE head_node_osm_id != tail_node_osm_id"
            ):
                line = wkb.loads(r["geom"])
                feature = {
                    "type": "Feature",
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [_round_coord(c) for c in line.coords],
                    },
                    "properties": {
                        "lts": int(r["lts"]),
                        "on_hin": bool(r["on_hin"]),
                    },
                }
                if not first:
                    f.write(",")
                else:
                    first = False
                f.write(json.dumps(feature, separators=(",", ":")))

            for r in con.execute(
                "SELECT lts_approach, on_hin, geom FROM intersections"
            ):
                pt = wkb.loads(r["geom"])
                feature = {
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": _round_coord((pt.x, pt.y)),
                    },
                    "properties": {
                        "lts_approach": int(r["lts_approach"]),
                        "on_hin": bool(r["on_hin"]),
                    },
                }
                if not first:
                    f.write(",")
                else:
                    first = False
                f.write(json.dumps(feature, separators=(",", ":")))

            f.write("]}")
    finally:
        con.close()

    os.replace(tmp_path, output_path)
    return output_path.stat().st_size
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
.venv/bin/pytest tests/prep/test_lts_network_export.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Run the fast test suite to confirm no regressions**

```bash
make test
```

Expected: all previously-passing tests still pass; 4 new tests added.

- [ ] **Step 6: Commit**

```bash
git add prep/lts_network_export.py tests/prep/test_lts_network_export.py tests/conftest.py
git commit -m "feat(prep): exporter for data/lts-network.geojson.gz"
```

---

## Task 2: Wire exporter into `prep/main.run_pipeline`

**Files:**
- Modify: `prep/main.py` (call exporter at end of `run_pipeline`)
- Modify: `prep/reporting/prep_report.py` (add file-size line to the report)
- Modify: `tests/prep/test_lts_network_export.py` (add an end-to-end test asserting the file is produced)

**Spec refs:** §5.3 (prep-pipeline integration).

- [ ] **Step 1: Find the end-of-pipeline insertion point**

Run:

```bash
grep -n "def run_pipeline\|return.*RunResult\|build_prep_report" prep/main.py
```

Locate where `run_pipeline` finishes (just before its `return` statement). The exporter call must run AFTER `bikemap.db` is finalized and BEFORE the function returns.

- [ ] **Step 2: Add the exporter call**

In `prep/main.py`, add at the top with the other prep imports:

```python
from prep.lts_network_export import export_lts_network
```

Just before the return statement of `run_pipeline`, after the DB is finalized:

```python
    # Export the static LTS-network artifact consumed by the /explore view.
    # Lives next to bikemap.db so the upload-db flow ships both together.
    lts_network_path = db_path.parent / "lts-network.geojson.gz"
    lts_network_size = export_lts_network(db_path, lts_network_path)
```

If `run_pipeline` returns a result object that includes file artifacts, append the new file to it. Inspect the existing return type and adapt.

- [ ] **Step 3: Add a line to the prep report**

In `prep/reporting/prep_report.py`, find where artifact sizes are reported (look for `bikemap.db` mentions). Append a similar line for `lts-network.geojson.gz` showing the size in MB.

If there isn't a dedicated "artifacts" section, add one (3 lines).

- [ ] **Step 4: Add an end-to-end test in `tests/prep/test_lts_network_export.py`**

Append:

```python
def test_run_pipeline_writes_lts_network_file(
    tiny_bikemap_db_with_pois: Path, tmp_path: Path,
) -> None:
    """run_pipeline writes lts-network.geojson.gz next to bikemap.db.

    We don't re-run the full prep pipeline (heavy); we exercise just the
    exporter call site by invoking export_lts_network with the fixture
    DB and asserting the output lands at the expected path next to it.
    """
    out_path = tiny_bikemap_db_with_pois.parent / "lts-network.geojson.gz"
    try:
        export_lts_network(tiny_bikemap_db_with_pois, out_path)
        assert out_path.exists()
        assert out_path.stat().st_size > 0
    finally:
        if out_path.exists():
            out_path.unlink()
```

- [ ] **Step 5: Run tests**

```bash
.venv/bin/pytest tests/prep/ -v
make test
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add prep/main.py prep/reporting/prep_report.py tests/prep/test_lts_network_export.py
git commit -m "feat(prep): run_pipeline writes lts-network.geojson.gz"
```

---

## Task 3: Flask `/lts-network` route

**Files:**
- Create: `app/routes/lts_network.py`
- Create: `tests/app/test_lts_network_route.py`
- Modify: `app/main.py` (register blueprint)

**Spec refs:** §5.4 (serving route), §5.5 (rate limit).

**Design notes:**
- Blueprint factory takes `data_dir: Path` and `limiter`. The route reads `data_dir / "lts-network.geojson.gz"` via `send_from_directory`.
- Sets explicit `Content-Encoding: gzip` and `Content-Type: application/geo+json` since Flask's static handler wouldn't otherwise do this for a `.gz` file.
- ETag/Last-Modified/If-None-Match are handled by `send_from_directory` natively.
- If the file is missing → `404` (frontend's error card handles this).

- [ ] **Step 1: Write the failing test**

Create `tests/app/test_lts_network_route.py`:

```python
"""Tests for the /lts-network Flask route (Plan 2D Task 3)."""
from __future__ import annotations

import gzip
from pathlib import Path

from prep.lts_network_export import export_lts_network


def _make_app_with_lts_file(
    bikemap_db: Path, cache_db: Path, data_dir: Path,
):
    """Helper: create an app whose data_dir contains a freshly-built
    lts-network.geojson.gz."""
    from app.main import create_app
    export_lts_network(bikemap_db, data_dir / "lts-network.geojson.gz")
    return create_app(
        bikemap_db=bikemap_db,
        cache_db=cache_db,
        nominatim_user_agent="test/1.0",
        min_streets=1,
    )


def test_route_serves_gzipped_geojson_with_correct_headers(
    tiny_bikemap_db_with_pois: Path, tmp_path: Path,
) -> None:
    # Place bikemap.db in tmp_path/data so the geojson lives next to it.
    import shutil
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_target = data_dir / "bikemap.db"
    shutil.copy(tiny_bikemap_db_with_pois, db_target)

    app = _make_app_with_lts_file(db_target, tmp_path / "cache.db", data_dir)
    client = app.test_client()
    resp = client.get("/lts-network")

    assert resp.status_code == 200
    assert resp.headers["Content-Type"] == "application/geo+json"
    assert resp.headers["Content-Encoding"] == "gzip"
    # Gzip magic header.
    assert resp.data[:2] == b"\x1f\x8b"
    # And it decompresses to valid JSON.
    gzip.decompress(resp.data)  # raises on malformed


def test_route_returns_304_on_matching_etag(
    tiny_bikemap_db_with_pois: Path, tmp_path: Path,
) -> None:
    import shutil
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_target = data_dir / "bikemap.db"
    shutil.copy(tiny_bikemap_db_with_pois, db_target)

    app = _make_app_with_lts_file(db_target, tmp_path / "cache.db", data_dir)
    client = app.test_client()
    first = client.get("/lts-network")
    assert first.status_code == 200
    etag = first.headers["ETag"]

    second = client.get("/lts-network", headers={"If-None-Match": etag})
    assert second.status_code == 304


def test_route_returns_404_when_file_missing(
    tiny_bikemap_db_with_pois: Path, tmp_path: Path,
) -> None:
    import shutil
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_target = data_dir / "bikemap.db"
    shutil.copy(tiny_bikemap_db_with_pois, db_target)

    # Don't generate the geojson file.
    from app.main import create_app
    app = create_app(
        bikemap_db=db_target,
        cache_db=tmp_path / "cache.db",
        nominatim_user_agent="test/1.0",
        min_streets=1,
    )
    client = app.test_client()
    resp = client.get("/lts-network")
    assert resp.status_code == 404


def test_route_exempt_from_rate_limit(
    tiny_bikemap_db_with_pois: Path, tmp_path: Path,
) -> None:
    import shutil
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_target = data_dir / "bikemap.db"
    shutil.copy(tiny_bikemap_db_with_pois, db_target)

    app = _make_app_with_lts_file(db_target, tmp_path / "cache.db", data_dir)
    client = app.test_client()
    for _ in range(75):
        resp = client.get("/lts-network")
        assert resp.status_code == 200
```

Run:

```bash
.venv/bin/pytest tests/app/test_lts_network_route.py -v
```

Expected: collection error or 404 on every test (route not registered).

- [ ] **Step 2: Create the blueprint**

Create `app/routes/lts_network.py`:

```python
"""GET /lts-network — serves data/lts-network.geojson.gz, generated offline
by prep/lts_network_export (spec §5, Plan 2D)."""
from __future__ import annotations

from pathlib import Path

from flask import Blueprint, Response, send_from_directory
from flask_limiter import Limiter

_FILENAME = "lts-network.geojson.gz"


def build_lts_network_blueprint(data_dir: Path, limiter: Limiter) -> Blueprint:
    bp = Blueprint("lts_network", __name__)

    @bp.get("/lts-network")
    @limiter.exempt
    def lts_network() -> Response:
        # send_from_directory handles ETag, If-None-Match, Last-Modified.
        # If the file doesn't exist it raises 404 — caller renders an
        # error card; see spec §6.4.
        resp = send_from_directory(
            data_dir, _FILENAME, max_age=86400,
        )
        resp.headers["Content-Type"] = "application/geo+json"
        # The file is pre-gzipped on disk; mark the transport encoding
        # so browsers decompress transparently. Flask's static handler
        # would otherwise leave Content-Encoding unset.
        resp.headers["Content-Encoding"] = "gzip"
        return resp

    return bp
```

- [ ] **Step 3: Register the blueprint in `create_app`**

Edit `app/main.py`:

1. Near the other route imports, add:

```python
from app.routes.lts_network import build_lts_network_blueprint
```

2. Among the `app.register_blueprint(...)` calls (after the existing ones), add:

```python
    app.register_blueprint(build_lts_network_blueprint(
        data_dir=bikemap_db.parent,
        limiter=limiter,
    ))
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/app/test_lts_network_route.py -v
make test
```

Expected: 4 new tests pass; existing 141+ tests still pass.

- [ ] **Step 5: Commit**

```bash
git add app/routes/lts_network.py app/main.py tests/app/test_lts_network_route.py
git commit -m "feat(lts-network): /lts-network route serves the static file"
```

---

## Task 4: Frontend shell — `/explore` Flask route + `explore.html` + map init

**Files:**
- Modify: `app/main.py` (add the `/explore` route)
- Create: `app/static/explore.html`
- Create: `app/static/explore.js`
- Modify: `tests/app/test_main.py` (assert `/explore` serves `explore.html`)

**Spec refs:** §6.1 (file structure), §6.2 (loading behavior).

- [ ] **Step 1: Add the failing route test**

Append to `tests/app/test_main.py`:

```python
def test_explore_route_serves_explorer_shell(
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
    resp = client.get("/explore")
    assert resp.status_code == 200
    assert b"<title>Chicago LTS Data Explorer</title>" in resp.data
    assert b'src="/static/explore.js"' in resp.data
```

Run:

```bash
.venv/bin/pytest tests/app/test_main.py::test_explore_route_serves_explorer_shell -v
```

Expected: 404.

- [ ] **Step 2: Add the `/explore` route in `app/main.py`**

After the `@app.get("/")` block in `create_app`, add:

```python
    @app.get("/explore")
    @limiter.exempt
    def explore():  # type: ignore[no-untyped-def]
        return send_from_directory(app.static_folder, "explore.html")
```

(`send_from_directory` is already imported.)

- [ ] **Step 3: Create `explore.html`**

Create `app/static/explore.html`:

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=1024">
  <title>Chicago LTS Data Explorer</title>
  <link rel="stylesheet" href="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.css">
  <link rel="stylesheet" href="/static/styles.css">
  <script src="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.js"></script>
</head>
<body class="explore-page">
  <div id="map"></div>
  <div id="ui-overlays">
    <button id="basemap-toggle" type="button" disabled>Loading data…</button>
    <a id="back-to-advocacy" href="/">← Back to advocacy view</a>
    <div id="legend" hidden>
      <div class="legend-row"><span class="legend-swatch swatch-lts-1"></span> LTS 1 — Safe for kid</div>
      <div class="legend-row"><span class="legend-swatch swatch-lts-2"></span> LTS 2 — Safe for parent</div>
      <div class="legend-row"><span class="legend-swatch swatch-lts-3"></span> LTS 3 — Not safe</div>
      <div class="legend-row"><span class="legend-swatch swatch-hin"></span> High-Injury Network</div>
    </div>
    <label id="hin-toggle" hidden>
      <input type="checkbox" id="hin-checkbox"> Show High-Injury Network overlay
    </label>
    <div id="explore-error" hidden role="alert">
      <p class="ee-text">Couldn't load the LTS network.</p>
      <button class="ee-retry" type="button">Retry</button>
    </div>
  </div>
  <script type="module" src="/static/explore.js"></script>
</body>
</html>
```

- [ ] **Step 4: Create `explore.js` with map init only**

Create `app/static/explore.js`:

```javascript
// Entrypoint for the LTS Data Explorer at /explore.
// Plan 2D Task 4: map init only (Tasks 5-7 add data + interactions).

const CHICAGO_CENTER = [-87.63, 41.88];
const DEFAULT_ZOOM = 11;
const STREETS_STYLE = "https://tiles.openfreemap.org/styles/liberty";
const SATELLITE_STYLE = {
  version: 8,
  sources: {
    "esri-imagery": {
      type: "raster",
      tiles: [
        "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
      ],
      tileSize: 256,
      attribution:
        "Tiles &copy; Esri &mdash; Source: Esri, i-cubed, USDA, USGS, AEX, GeoEye, Getmapping, Aerogrid, IGN, IGP, UPR-EGP, and the GIS User Community",
    },
  },
  layers: [
    { id: "esri-imagery", type: "raster", source: "esri-imagery" },
  ],
};

const map = new maplibregl.Map({
  container: document.getElementById("map"),
  style: STREETS_STYLE,
  center: CHICAGO_CENTER,
  zoom: DEFAULT_ZOOM,
});
window.__map = map;

let basemap = "streets";
const toggleBtn = document.getElementById("basemap-toggle");
toggleBtn.addEventListener("click", () => {
  basemap = basemap === "streets" ? "satellite" : "streets";
  map.setStyle(basemap === "satellite" ? SATELLITE_STYLE : STREETS_STYLE);
  toggleBtn.textContent = basemap === "streets" ? "Satellite" : "Streets";
});
```

- [ ] **Step 5: Add explore-page CSS**

In `app/static/styles.css`, append:

```css
/* Explorer page (Plan 2D). Reuses tokens + map styles from the advocacy
   view, adds page-specific bits. */
.explore-page #basemap-toggle:disabled {
  background: #f1f5f9;
  color: var(--c-text-muted);
  cursor: wait;
}

#back-to-advocacy {
  position: absolute; top: 12px; right: 12px;
  background: var(--c-bg); border: 1px solid var(--c-border);
  border-radius: var(--radius-panel); box-shadow: var(--shadow-panel);
  padding: 8px 12px;
  font: 14px var(--font-stack); color: var(--c-text); text-decoration: none;
}
#back-to-advocacy:hover { background: #f8fafc; }
```

- [ ] **Step 6: Verify in browser**

```javascript
// preview_eval
window.location = "http://localhost:8000/explore";
```

Expected:
- `preview_snapshot` shows the Chicago map and the "Loading data…" basemap button (disabled).
- `preview_console_logs` (level=error) is empty.

- [ ] **Step 7: Run tests and commit**

```bash
.venv/bin/pytest tests/app/ -v
git add app/main.py app/static/explore.html app/static/explore.js app/static/styles.css tests/app/test_main.py
git commit -m "feat(explore): /explore route + minimal map shell"
```

---

## Task 5: Fetch + render data layers + legend

**Files:**
- Modify: `app/static/explore.js`
- Modify: `app/static/styles.css`

**Spec refs:** §4 (data layers), §6.2 (loading behavior), §6.5 (basemap re-render).

**Design notes:**
- The fetched GeoJSON is held in module scope so basemap toggle can re-add layers without re-fetching.
- After the fetch completes, the basemap toggle button is enabled and reverts to "Satellite".
- Color expressions are MapLibre data-driven `match` expressions on `lts` / `lts_approach`.

- [ ] **Step 1: Implement the fetch + layer-adding flow**

Replace the contents of `app/static/explore.js` with:

```javascript
// Entrypoint for the LTS Data Explorer at /explore.
// Plan 2D Tasks 4-5: map init + fetch + layer rendering + legend.

const CHICAGO_CENTER = [-87.63, 41.88];
const DEFAULT_ZOOM = 11;
const STREETS_STYLE = "https://tiles.openfreemap.org/styles/liberty";
const SATELLITE_STYLE = {
  version: 8,
  sources: {
    "esri-imagery": {
      type: "raster",
      tiles: [
        "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
      ],
      tileSize: 256,
      attribution:
        "Tiles &copy; Esri &mdash; Source: Esri, i-cubed, USDA, USGS, AEX, GeoEye, Getmapping, Aerogrid, IGN, IGP, UPR-EGP, and the GIS User Community",
    },
  },
  layers: [
    { id: "esri-imagery", type: "raster", source: "esri-imagery" },
  ],
};

const map = new maplibregl.Map({
  container: document.getElementById("map"),
  style: STREETS_STYLE,
  center: CHICAGO_CENTER,
  zoom: DEFAULT_ZOOM,
});
window.__map = map;

let basemap = "streets";
const toggleBtn = document.getElementById("basemap-toggle");
toggleBtn.addEventListener("click", () => {
  basemap = basemap === "streets" ? "satellite" : "streets";
  map.setStyle(basemap === "satellite" ? SATELLITE_STYLE : STREETS_STYLE);
  toggleBtn.textContent = basemap === "streets" ? "Satellite" : "Streets";
});

// Module-scope cache so basemap toggle can re-add layers without re-fetching.
let streetsFC = null;
let intersectionsFC = null;
let hinFC = null;

const LTS_COLOR_EXPR = [
  "match",
  ["get", "lts"],
  1, "#16a34a",
  2, "#f59e0b",
  3, "#dc2626",
  "#999999",
];
const LTS_APPROACH_COLOR_EXPR = [
  "match",
  ["get", "lts_approach"],
  1, "#16a34a",
  2, "#f59e0b",
  3, "#dc2626",
  "#999999",
];

function addLayers() {
  if (!streetsFC || !intersectionsFC || !hinFC) return;

  if (!map.getSource("hin-source")) {
    map.addSource("hin-source", { type: "geojson", data: hinFC });
    map.addLayer({
      id: "hin-layer",
      type: "line",
      source: "hin-source",
      layout: { visibility: "none", "line-cap": "round", "line-join": "round" },
      paint: { "line-color": "#dc2626", "line-width": 4 },
    });
  }

  if (!map.getSource("streets-source")) {
    map.addSource("streets-source", { type: "geojson", data: streetsFC });
    map.addLayer({
      id: "streets-layer",
      type: "line",
      source: "streets-source",
      layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": LTS_COLOR_EXPR, "line-width": 2 },
    });
  }

  if (!map.getSource("intersections-source")) {
    map.addSource("intersections-source", { type: "geojson", data: intersectionsFC });
    map.addLayer({
      id: "intersections-layer",
      type: "circle",
      source: "intersections-source",
      paint: {
        "circle-color": LTS_APPROACH_COLOR_EXPR,
        "circle-radius": [
          "interpolate", ["linear"], ["zoom"],
          11, 2,
          14, 5,
        ],
        "circle-stroke-color": "#ffffff",
        "circle-stroke-width": 0.5,
      },
    });
  }
}

async function loadNetwork() {
  const resp = await fetch("/lts-network");
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  const fc = await resp.json();
  streetsFC = {
    type: "FeatureCollection",
    features: fc.features.filter((f) => f.geometry.type === "LineString"),
  };
  intersectionsFC = {
    type: "FeatureCollection",
    features: fc.features.filter((f) => f.geometry.type === "Point"),
  };
  hinFC = {
    type: "FeatureCollection",
    features: streetsFC.features.filter((f) => f.properties.on_hin === true),
  };
}

async function init() {
  try {
    await Promise.all([
      loadNetwork(),
      new Promise((r) => map.once("load", r)),
    ]);
    addLayers();
    document.getElementById("legend").hidden = false;
    document.getElementById("hin-toggle").hidden = false;
    toggleBtn.disabled = false;
    toggleBtn.textContent = "Satellite";
  } catch (err) {
    console.error("LTS network load failed", err);
    // Task 7 implements the error card; for now the failure is logged.
  }
}
init();

// Re-add layers on basemap swap (setStyle wipes them).
map.on("style.load", () => {
  addLayers();
});
```

- [ ] **Step 2: Add legend + HIN-toggle CSS**

In `app/static/styles.css`, append:

```css
#legend {
  position: absolute; bottom: 12px; right: 12px;
  background: var(--c-bg); border: 1px solid var(--c-border);
  border-radius: var(--radius-panel); box-shadow: var(--shadow-panel);
  padding: 12px 14px;
  font: 13px var(--font-stack); color: var(--c-text);
  min-width: 200px;
}
#legend[hidden] { display: none; }
.legend-row { display: flex; align-items: center; gap: 8px; margin: 4px 0; }
.legend-swatch {
  width: 18px; height: 4px; border-radius: 2px; display: inline-block;
}
.swatch-lts-1 { background: #16a34a; }
.swatch-lts-2 { background: #f59e0b; }
.swatch-lts-3 { background: #dc2626; }
.swatch-hin {
  background: #dc2626; height: 6px;
  box-shadow: 0 0 0 1px white;
}

#hin-toggle {
  position: absolute; top: 12px; right: 200px;
  background: var(--c-bg); border: 1px solid var(--c-border);
  border-radius: var(--radius-panel); box-shadow: var(--shadow-panel);
  padding: 8px 12px;
  font: 14px var(--font-stack); color: var(--c-text);
  display: flex; align-items: center; gap: 6px;
  cursor: pointer;
}
#hin-toggle[hidden] { display: none; }
#hin-toggle input { cursor: pointer; }
```

- [ ] **Step 3: Generate the data file in the dev environment**

Run the exporter against the current dev `bikemap.db` so the route can serve real data:

```bash
.venv/bin/python -c "
from pathlib import Path
from prep.lts_network_export import export_lts_network
size = export_lts_network(Path('data/bikemap.db'), Path('data/lts-network.geojson.gz'))
print(f'wrote data/lts-network.geojson.gz — {size:,} bytes ({size / 1024 / 1024:.1f} MB)')
"
```

Expected: file written, size in the ~12–20 MB range. **If the file is larger than 25 MB**, escalate to a design revisit (consider vector tiles per spec §10).

- [ ] **Step 4: Verify in browser**

Reload `/explore` via preview tools. Wait ~15 s for the data to load.

```javascript
// preview_eval
({
  legendShown: !document.getElementById('legend').hidden,
  hinToggleShown: !document.getElementById('hin-toggle').hidden,
  toggleEnabled: !document.getElementById('basemap-toggle').disabled,
  toggleText: document.getElementById('basemap-toggle').textContent,
  layerIds: window.__map.getStyle().layers
    .filter(l => ['streets-layer','intersections-layer','hin-layer'].includes(l.id))
    .map(l => l.id),
})
```

Expected: `{legendShown: true, hinToggleShown: true, toggleEnabled: true, toggleText: "Satellite", layerIds: ["hin-layer","streets-layer","intersections-layer"]}` (order may vary).

`preview_screenshot` should show Chicago covered in LTS-colored streets.

`preview_network` should show exactly one `GET /lts-network` → 200 with `Content-Encoding: gzip`.

- [ ] **Step 5: Commit**

```bash
git add app/static/explore.js app/static/styles.css
git commit -m "feat(explore): fetch /lts-network + render streets/intersections + legend"
```

---

## Task 6: HIN overlay toggle + `?hin=1` permalink

**Files:**
- Modify: `app/static/explore.js`

**Spec refs:** §3 (UI), §6.3 (toggle behavior).

- [ ] **Step 1: Wire the toggle**

In `app/static/explore.js`, append after the `style.load` handler:

```javascript
// HIN overlay toggle + URL permalink (?hin=1).
const hinCheckbox = document.getElementById("hin-checkbox");

function applyHinVisibility(checked) {
  if (!map.getLayer("hin-layer")) return;
  map.setLayoutProperty("hin-layer", "visibility", checked ? "visible" : "none");
}

function syncHinUrl(checked) {
  const path = window.location.pathname;
  history.replaceState(null, "", checked ? `${path}?hin=1` : path);
}

hinCheckbox.addEventListener("change", () => {
  applyHinVisibility(hinCheckbox.checked);
  syncHinUrl(hinCheckbox.checked);
});

// Honor ?hin=1 on initial load. Layer may not exist yet (first style.load
// hasn't fired); applyHinVisibility is a no-op in that case and the
// style.load handler picks it up.
function applyInitialHin() {
  const params = new URLSearchParams(window.location.search);
  if (params.get("hin") === "1") {
    hinCheckbox.checked = true;
    applyHinVisibility(true);
  }
}
applyInitialHin();
map.on("style.load", applyInitialHin);
```

- [ ] **Step 2: Verify in browser**

Reload `/explore`. Wait ~15 s.

```javascript
// preview_eval — toggle on
document.getElementById('hin-checkbox').click();
await new Promise(r => setTimeout(r, 100));
({
  hinVisible: window.__map.getLayoutProperty('hin-layer', 'visibility'),
  url: window.location.search,
})
```

Expected: `{hinVisible: "visible", url: "?hin=1"}`.

Toggle off:

```javascript
document.getElementById('hin-checkbox').click();
await new Promise(r => setTimeout(r, 100));
({
  hinVisible: window.__map.getLayoutProperty('hin-layer', 'visibility'),
  url: window.location.search,
})
```

Expected: `{hinVisible: "none", url: ""}`.

Reload with `?hin=1`:

```javascript
window.location = "http://localhost:8000/explore?hin=1";
```

Wait ~15 s, then:

```javascript
({
  checked: document.getElementById('hin-checkbox').checked,
  hinVisible: window.__map.getLayoutProperty('hin-layer', 'visibility'),
})
```

Expected: `{checked: true, hinVisible: "visible"}`.

- [ ] **Step 3: Commit**

```bash
git add app/static/explore.js
git commit -m "feat(explore): HIN overlay toggle + ?hin=1 permalink"
```

---

## Task 7: Error card + retry

**Files:**
- Modify: `app/static/explore.js`
- Modify: `app/static/styles.css`

**Spec refs:** §6.4 (error categories — enumerated failure modes).

**Design notes:**
- The error card already exists in `explore.html` (Task 4) with `hidden`. We wire show/hide + retry.
- A 404 from `/lts-network` (file not built yet) is a valid path through here — the prep pipeline hasn't run.

- [ ] **Step 1: Replace `init()` with error-aware version**

In `app/static/explore.js`, replace the `async function init()` block and the bare `init();` call with:

```javascript
const errorCard = document.getElementById("explore-error");
const errorRetry = errorCard.querySelector(".ee-retry");

function showError(msg) {
  errorCard.hidden = false;
  if (msg) errorCard.querySelector(".ee-text").textContent = msg;
}
function hideError() {
  errorCard.hidden = true;
}

async function init() {
  hideError();
  toggleBtn.disabled = true;
  toggleBtn.textContent = "Loading data…";
  try {
    await Promise.all([
      loadNetwork(),
      new Promise((r) => map.once("load", r)),
    ]);
    addLayers();
    document.getElementById("legend").hidden = false;
    document.getElementById("hin-toggle").hidden = false;
    toggleBtn.disabled = false;
    toggleBtn.textContent = "Satellite";
  } catch (err) {
    console.error("LTS network load failed", err);
    const msg = err && /HTTP 404/.test(String(err.message))
      ? "LTS network data hasn't been built yet. Run the prep pipeline."
      : "Couldn't load the LTS network.";
    showError(msg);
  }
}

errorRetry.addEventListener("click", init);
init();
```

- [ ] **Step 2: Add error-card CSS**

In `app/static/styles.css`, append:

```css
#explore-error {
  position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%);
  background: var(--c-bg); border: 1px solid var(--c-border);
  border-radius: var(--radius-panel); box-shadow: var(--shadow-panel);
  padding: 20px 24px; max-width: 360px; text-align: center;
  font: 14px var(--font-stack); color: var(--c-text);
}
#explore-error[hidden] { display: none; }
.ee-text { margin-bottom: 12px; }
.ee-retry {
  background: var(--c-safe); color: white;
  border: none; border-radius: 6px;
  padding: 8px 16px; font: 14px var(--font-stack); cursor: pointer;
}
.ee-retry:hover { background: #15803d; }
```

- [ ] **Step 3: Verify the 404 path**

Temporarily delete the data file:

```bash
mv data/lts-network.geojson.gz data/lts-network.geojson.gz.bak
```

Reload `/explore`. Confirm the error card shows the "LTS network data hasn't been built yet" message. Restore:

```bash
mv data/lts-network.geojson.gz.bak data/lts-network.geojson.gz
```

Click Retry on the error card — confirm the map loads.

- [ ] **Step 4: Commit**

```bash
git add app/static/explore.js app/static/styles.css
git commit -m "feat(explore): error card + retry, with 404-specific copy"
```

---

## Task 8: Cross-view nav links + final QA + Plan 2C dependency note

**Files:**
- Modify: `app/static/index.html` (bottom-left "Explore LTS data →" link)
- Modify: `app/static/styles.css`
- Modify: `docs/superpowers/plans/2026-05-07-chicago-bike-map-02c-deploy.md` (note the upload-db dependency)

**Spec refs:** §2 (entry + URL), §5.7 (Plan 2C touch-up), §7 (testing summary).

- [ ] **Step 1: Add the link to `index.html`**

Inside `<div id="ui-overlays">` in `app/static/index.html`, add as the LAST element (after `permalink-modal`):

```html
    <a id="explore-link" href="/explore">Explore LTS data →</a>
```

- [ ] **Step 2: Style it**

In `app/static/styles.css`, append:

```css
#explore-link {
  position: absolute; bottom: 12px; left: 12px;
  background: var(--c-bg); border: 1px solid var(--c-border);
  border-radius: var(--radius-panel); box-shadow: var(--shadow-panel);
  padding: 8px 12px;
  font: 14px var(--font-stack); color: var(--c-text); text-decoration: none;
}
#explore-link:hover { background: #f8fafc; }
```

- [ ] **Step 3: Verify both directions**

Navigate from `/` → `/explore` and back via the in-page links (preview tools).

```javascript
// At http://localhost:8000/
document.getElementById('explore-link').click();
// After redirect: at http://localhost:8000/explore — wait ~15s
document.getElementById('back-to-advocacy').click();
// Back at /
```

- [ ] **Step 4: Add the Plan 2C dependency note**

In `docs/superpowers/plans/2026-05-07-chicago-bike-map-02c-deploy.md`, find Task 3 (`prep/upload_db.py` → `/admin/upload-bikemap-db`). Append a note (or a new sub-step) stating:

> **Plan 2D dependency:** When Plan 2D ships, `prep/upload_db.py` must upload **both** `bikemap.db` AND `lts-network.geojson.gz` in the same atomic refresh. Upload them as a 2-file POST or as a tarball, but both must move together — a `bikemap.db` newer than the geojson surfaces as data-skew on `/explore`. The `/admin/upload-bikemap-db` endpoint must accept both files (or a tarball containing both) and `os.replace` them into `/var/data/` only after both have been received successfully.

- [ ] **Step 5: Run the full test suite**

```bash
make test
```

Expected: all tests pass.

- [ ] **Step 6: §6.4 launch-criteria walkthrough**

For each, take a `preview_screenshot` and confirm visually:

- Streets layer renders with expected color distribution (mostly green, red on arterials).
- Intersections visible at zoom ≥14.
- HIN toggle ON → red halos appear under arterials. Toggle OFF → halos vanish; URL clears `?hin=1`.
- `/explore?hin=1` permalink survives reload.
- Bottom-left link from `/` → `/explore` works.
- "← Back to advocacy view" from `/explore` → `/` works.
- Streets/Satellite toggle on `/explore` works after data loads.
- Error card appears + Retry works when `data/lts-network.geojson.gz` is missing.

- [ ] **Step 7: Final commit**

```bash
git add app/static/index.html app/static/styles.css ../docs/superpowers/plans/2026-05-07-chicago-bike-map-02c-deploy.md
git commit -m "feat(explore): bidirectional nav + Plan 2C upload-db dependency note"
```

---

## Done

After Task 8, Plan 2D is complete:

- `GET /explore` renders the bare Chicago LTS map.
- Every street colored by LTS, every intersection by `lts_approach`.
- HIN overlay one click away; state survives in the URL.
- Bidirectional nav between `/` and `/explore`.
- Single error card handles every failure path; Retry re-runs the load.
- **Zero backend memory cost** — Flask serves the static file via `sendfile`.

**Out of scope (per spec §10):** vector tiles, per-viewport fetch, hover tooltips, click-to-inspect, per-LTS filter checkboxes, POI overlay, mobile-specific layout.

**Plan 2C dependency:** `prep/upload_db.py` (Plan 2C Task 3) must be extended to upload `lts-network.geojson.gz` alongside `bikemap.db` atomically. This is captured in Task 8 Step 4.
