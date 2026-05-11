# LTS Data Explorer view — Design

**Date**: 2026-05-11
**Status**: Spec lock — awaiting user review before implementation planning
**Scope**: An addition to the Chicago Bike Advocacy Map ([2026-05-04 main spec](2026-05-04-chicago-bike-advocacy-map-design.md)). Adds a third top-level view alongside Overview and Drill-down.

---

## 1. Purpose

A standalone view that shows the underlying network data — every street colored by LTS, every intersection by `lts_approach`, with an optional HIN overlay — so advocates, planners, and journalists can inspect the raw infrastructure without going through the home/destination/route flow.

The advocacy view (Overview + Drill-down) answers "what should change to make *my* trips safer?". The Explorer view answers "what does the city's bike-stress map actually look like?". Different audiences, different cognitive load, different defaults — therefore a separate view.

## 2. Entry + URL

- Served at `GET /explore` by Flask (`send_from_directory` returns `app/static/explore.html`).
- Discoverable via a small **"Explore LTS data →"** link **bottom-left** of the main app at `/`. Top-right is reserved for the approximate-home badge (Task 10) and the eventual legend; bottom-center is reserved for the gap-loading widget; bottom-left is otherwise unclaimed.
- The Explorer view does NOT use the lz-string permalink scheme. Its only encoded state is the HIN-overlay toggle, kept as a plain `?hin=1` query string for simplicity and shareability.

## 3. UI layout

A bare map filling the viewport, with three overlays:

- **Top-left**: Streets / Satellite basemap toggle (same control as the main app).
- **Top-right**: Single checkbox — "**Show High-Injury Network overlay**". Default unchecked.
- **Bottom-right**: Legend showing:
  - LTS 1 (green) — Safe for kid
  - LTS 2 (amber) — Safe for parent
  - LTS 3 (red) — Not safe
  - HIN segment (when toggle on) — bold red outline
  - Intersection stress (circle samples)
- **Top-right corner of main `/` view**: Link back: "← Back to advocacy view".

No tier selector, no home input, no destinations, no fact panel, no permalink modal.

## 4. Data layers

Three MapLibre layers, rendered in this order (bottom to top):

1. **Streets layer** (always on).
   - Source: `/lts-network` GeoJSON, filtered to `Feature.geometry.type === "LineString"`.
   - Paint: `line-color` driven by `lts` property — LTS 1 = `#16a34a` (--c-safe), LTS 2 = `#f59e0b` (--c-fallback), LTS 3 = `#dc2626` (--c-stress). Width 2px at all zooms.

2. **HIN highlight layer** (toggleable).
   - Same source filtered to `lts ∈ {1,2,3}` AND `on_hin === true`.
   - Paint: `line-color: #dc2626`, width 4px, drawn beneath the streets layer so it reads as an "outline" / "halo".
   - Visibility flipped via `setLayoutProperty("visibility", checkbox.checked ? "visible" : "none")`.

3. **Intersections layer** (always on).
   - Source: `/lts-network` filtered to `Feature.geometry.type === "Point"`.
   - Paint: `circle-color` driven by `lts_approach` (same palette as streets). Radius 3px at zoom ≤13, 5px at zoom ≥14.

No interactivity (no hover, no click, no tooltips). Pan and zoom only.

## 5. Backend

### 5.1 Data source: static file built by the prep pipeline

The Explorer's data is a **single gzipped GeoJSON file** generated offline by the prep pipeline and shipped alongside `bikemap.db` to Render's persistent disk. Flask serves it via a tiny route that wraps `send_from_directory`. **No live endpoint, no startup-time data construction, no in-memory cache.**

The decision to move from a dynamic endpoint to a static file came from a critical review of the first-draft architecture; alternatives are recorded in §10.

```
data/
├── bikemap.db                # existing (Plan 1 output)
└── lts-network.geojson.gz    # new (Plan 2D, written by prep pipeline)
```

### 5.2 File format

A single GeoJSON `FeatureCollection` gzipped at write time:

```json
{
  "type": "FeatureCollection",
  "features": [
    { "type": "Feature",
      "geometry": { "type": "LineString", "coordinates": [[lon, lat], ...] },
      "properties": { "lts": 2, "on_hin": false } },
    { "type": "Feature",
      "geometry": { "type": "Point", "coordinates": [lon, lat] },
      "properties": { "lts_approach": 1, "on_hin": false } },
    ...
  ]
}
```

Streets come from `streets.geom` (WKB LineString in WGS84). Intersections come from `intersections.geom` (WKB Point in WGS84). The file contains ALL features; no bbox slicing.

**Coordinate precision:** Coordinates are rounded to **5 decimal places** (~1 m precision at Chicago's latitude). PFB emits 7 decimal places (~1 cm); the extra precision is invisible at the supported zoom levels (11–16). Rounding saves an estimated 25–30 % of pre-gzip bytes.

### 5.3 Prep-pipeline integration

A new step at the end of `prep/main.py`, after `bikemap.db` is finalized:

1. Open the just-built `bikemap.db` read-only.
2. Stream features into `gzip.GzipFile(open("data/lts-network.geojson.gz", "wb"))` — the uncompressed JSON never lives in memory as a complete string.
3. Log the resulting file size to `prep_report.md` alongside the other artifacts.

Build time cost: ~5–10 s on a developer laptop. Runs monthly. Acceptable.

### 5.4 Serving route

A single Flask route:

```
GET /lts-network
```

Returns the contents of `<data_dir>/lts-network.geojson.gz` via `send_from_directory`. The response has:

- `Content-Type: application/geo+json`
- `Content-Encoding: gzip` (Werkzeug auto-derives this from the `.gz` extension via `mimetypes`; the `Content-Type: application/geo+json` override IS load-bearing because the `.gz` suffix masks `.geojson` from `mimetypes`)
- `Content-Length` (Flask sets this from file size)
- `ETag` (Flask's static handler computes from file mtime+size — invalidates automatically when the prep pipeline writes a new version)
- `Cache-Control: public, max-age=86400`
- `If-None-Match` → `304 Not Modified` is handled by Flask's static handler automatically

If the file doesn't exist (server started before the prep pipeline ran), the route returns `404`. The frontend's error card handles this gracefully — see §6.4.

### 5.5 Rate limit

`/lts-network` is added to the unlimited list (like `/health`). Bandwidth-heavy but not compute-heavy.

### 5.6 Size + perf budget

| Quantity | Estimate | Reasoning |
|---|---|---|
| Uncompressed JSON size | ~80–120 MB | ~353 k streets × ~5 vertices avg × ~22 chars per coord pair (5-decimal rounding) + properties + ~307 k intersection points. |
| File on disk (gzipped) | **~12–20 MB** | Typical text gzip ratio 5–8×. |
| Steady-state app RSS contribution | **~0 MB** | Flask serves the file via `os.sendfile`; the bytes never enter Python memory. |
| Startup peak memory contribution | **0 MB** | No work at boot. |
| Prep-pipeline build time | ~5–10 s | Streams to `gzip.GzipFile`. Runs once per monthly refresh. |

**Time-to-loaded budget:**

| Phase | Target | Notes |
|---|---|---|
| First paint of basemap | < 1 s | Streets style URL, cached by browser |
| `/lts-network` time-to-first-byte | < 200 ms | `sendfile`-served static file |
| `/lts-network` time-to-fully-loaded | **< 15 s on typical broadband** | ~12–20 MB gzipped — 10 Mbps ≈ 12 s |
| Network features → MapLibre layers added | < 2 s | One-time JSON parse + addSource × 3 |
| Pan/zoom interaction | 60 fps | MapLibre's native rendering |

### 5.7 Deploy implications (Plan 2C touch-up)

Plan 2C Task 3 (`prep/upload_db.py` → `/admin/upload-bikemap-db` endpoint) should be extended to upload `lts-network.geojson.gz` alongside `bikemap.db` in the same atomic refresh. Both files must move together — a `bikemap.db` newer than the geojson would surface as data-skew on the Explorer for up to a day. The plan section will note this dependency.

## 6. Frontend

### 6.1 File structure

```
app/static/
├── index.html         # advocacy view (unchanged)
├── explore.html       # new — bare shell for the data explorer
├── explore.js         # new — entrypoint for /explore
├── overview.js        # unchanged
├── drilldown.js       # unchanged
├── state.js           # unchanged
├── api.js             # unchanged
└── styles.css         # add explorer-specific selectors at the bottom
```

`explore.js` imports nothing from the advocacy code (overview.js, state.js, drilldown.js). It needs only MapLibre + a single fetch call. This isolation keeps the explorer's bundle tiny and means changes to the advocacy view can't break it.

### 6.2 Loading behavior

1. Page loads → MapLibre initializes (Chicago, zoom 11) with the streets basemap.
2. While the map renders the basemap, `fetch('/lts-network')` runs in parallel.
3. On fetch success, parse JSON, partition features by geometry type, add three sources/layers.
4. Read `?hin=1` from the URL; set the HIN checkbox + layer visibility accordingly.

### 6.3 HIN toggle behavior

Checkbox change handler:
1. Set HIN layer visibility.
2. Update query param: `history.replaceState(null, "", checkbox.checked ? "?hin=1" : "/explore")`.

That's the entire permalink mechanism for this view.

### 6.4 Error handling

Any of these failure modes hit the same error card and a retry button:

- Network error (offline, DNS, etc. — `fetch` throws).
- Non-2xx response (`5xx` from the server, `404` if the endpoint is misconfigured).
- JSON parse error on the response body.
- MapLibre `addSource` / `addLayer` throws (malformed geometry, etc.).

> "Couldn't load the LTS network. [Retry]"

The page is unusable without the data; no progressive degradation. Retry re-runs `fetch('/lts-network')`; the browser revalidates via `If-None-Match` against Flask's ETag.

### 6.5 Basemap re-render on toggle + race with initial fetch

Same pattern as the advocacy view: `setStyle` wipes sources/layers. On every `style.load`, re-add the three explorer layers from the cached GeoJSON (kept in module scope after the first fetch).

To avoid a race where the user toggles the basemap *during* the initial `/lts-network` fetch and the response resolves into a stale style: the basemap toggle is **disabled** (button has `disabled` attribute) until the first fetch completes. Visible state cue: button text reads "Loading data…" until ready.

## 7. Testing

### 7.1 Backend

- One integration test against the existing `tiny_bikemap_db_with_pois` fixture:
  - `GET /lts-network` returns 200.
  - Response is valid JSON, has `type: "FeatureCollection"`.
  - Feature count matches `streets + intersections` row counts.
  - Each street feature has `properties.lts ∈ {1,2,3}` and `properties.on_hin` boolean.
  - Each intersection feature has `properties.lts_approach` and `properties.on_hin`.
  - `Content-Encoding: gzip` header is set and the body is decodable.

### 7.2 Frontend

No formal unit tests (consistent with Plan 2B). Browser verification via the preview tools:
- `/explore` renders the map.
- All three layers visible after fetch completes.
- HIN toggle hides/shows the HIN layer.
- Reloading with `?hin=1` starts with the toggle checked.
- Error card appears if the endpoint is artificially failed.
- Link from `/` to `/explore` and back works.

## 8. Performance budget

See the budget table in §5.6. Headline: a desktop user on typical broadband sees the colored network within **~15 seconds** of hitting `/explore`. Acceptable for the audience (advocates/planners), who tolerate a brief load for a city-scale view. The static-file architecture eliminates all backend memory and CPU risk that the live-endpoint draft carried.

## 9. Out of scope (v1)

These were proposed during brainstorming and explicitly cut:

- POI overlay (schools/parks/etc.) on the Explorer view. The Explorer is for infrastructure; POIs belong in the advocacy flow.
- Click-to-inspect or hover tooltips. Pure visualization only.
- Per-LTS filter checkboxes ("hide LTS 1"). Visual filtering is the user's job via cognitive attention; we don't add UI for it.
- Vector tiles, per-viewport fetching, or any backend dynamic-data layer. Static GeoJSON dump is sufficient at Chicago's scale.
- Citywide aggregate metrics ("X% of Chicago is LTS 3"). Visualization, not analytics.
- Saving filter/viewport state in the URL beyond the HIN toggle. The advocacy view's lz-string permalink is overkill here.
- Mobile-specific layout. Desktop-only, consistent with the main spec.

## 10. Alternatives considered and rejected

These came up in brainstorming or in the spec self-review; recording them here so future readers don't re-litigate.

| Alternative | Why rejected |
|---|---|
| **Dynamic `/lts-network` endpoint, built at app startup** | First-draft architecture. Carried real risk: ~100 MB transient memory spike at boot + ~15 MB steady-state RSS contribution against an already-tight 480 MB sustained-load ceiling, plus boot-time JSON encoding of ~660 k features. Migration to a prep-pipeline-built static file eliminates all of this — the data only changes when `bikemap.db` is rebuilt, so a static artifact tracks the same refresh cycle for free. Recorded here so this isn't relitigated; see §5 for the chosen architecture. |
| **Per-viewport fetch (bbox query)** | Adds backend complexity and rejects the user's explicit "show me all the LTS data" framing. Revisit if the file size in §5.6 turns out to be wrong by 2× or more. |
| **Vector tiles (MVT via tippecanoe)** | Best user experience but adds a build-step toolchain (tippecanoe) and a per-zoom tile cache (~50–200 MB). Defer until the static-dump approach proves too slow. Migration is a clean swap of the MapLibre source from `geojson` to `vector` — no frontend rewrite. |
| **Hover tooltips with feature properties** | Cheap to implement (~30 lines) but explicitly cut per user direction. Reconsider if user testing surfaces "I want to know which street I'm hovering". |
| **Hide LTS-N filter checkboxes** | Cut for UI minimalism. A user can mentally filter by attention; we don't need a UI control for it. |
| **POI overlay (schools / parks / etc.)** | Explicitly cut by user — Explorer is for infrastructure, not destinations. |
| **Encoding viewport state in the URL** | The lz-string codec is overkill for a single boolean; `?hin=1` is enough. Map center/zoom is intentionally NOT encoded — users can always pan-zoom from city-center on every visit. |

## 11. Open questions

None at spec-lock. The brainstorming session + the self-review pass resolved every parameter.

## 12. Spec impact on the main project spec

Two small edits to `2026-05-04-chicago-bike-advocacy-map-design.md`:

1. Rename **§2 "Two-view interaction model"** to **§2 "Interaction model"** and add a new subsection **§2.5 "LTS Data Explorer view"** that summarizes the feature and links to this design.
2. Add a row to **§6.1 "In v1"** confirming the Explorer is in v1 scope.

No spec changes needed in §3 (data architecture), §4 (LTS routing), or §5 (system architecture) — the Explorer reuses the existing graph snapshot and adds one endpoint + one static page.
