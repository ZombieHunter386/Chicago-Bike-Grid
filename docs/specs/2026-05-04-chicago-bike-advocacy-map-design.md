# Chicago Bike Advocacy Map — Design

**Date**: 2026-05-04
**Status**: Spec lock — awaiting user review before implementation planning
**Repo target**: `ZombieHunter386/Lakeview-Bike-Grid` (may be renamed; scope is now citywide, not Lakeview-only)
**Hosting target**: Render Web Service Starter ($7/mo) + 1 GB Persistent Disk

---

## 0. Preamble

### 0.1 Canonical safety tier definitions

The product has exactly three user-facing safety tiers. They map informally to Geller's "Four Types of Cyclists" framework but use plain-English labels for the UI. **All other sections of this spec reference this table; do not duplicate the values elsewhere.**

| Tier name (UI) | LTS levels | LTS labels | Main weights | Fallback weights |
|---|---|---|---|---|
| **Safe for kid** | LTS 1 only | safe-for-kid only | `[1.0, ∞, ∞]` | `[1.0, 5.0, 20.0]` |
| **Safe for parent** | LTS 1–2 | safe-for-kid + safe-for-parent | `[1.0, 1.2, ∞]` | `[1.0, 1.2, 10.0]` |
| **Not safe** | LTS 1–3 | all (incl. not-safe) | `[1.0, 1.2, 1.5]` | `[1.0, 1.2, 1.5]` |

**LTS scale**: PFB City Ratings 2025 publishes 1-3 (collapsing original Mineta levels 3+4 into a single 'high stress' tier); we use that scale directly.

**LTS level labels** (for legend / detail panel):
- LTS 1 → "Safe for kid"
- LTS 2 → "Safe for parent"
- LTS 3 → "Not safe"

**Penalty principle**: LTS 1 = 1.0×, LTS 2 = 1.2×, LTS 3 = 1.5×. Tier controls which LTS levels are allowed; penalty is intrinsic to the LTS level.

**Hard cutoff rule**: main weights with `∞` (implemented as `1e9`) mean the tier *cannot* use those LTS levels. The router returns no path if no in-tier route exists.

**Fallback rule**: when the main route function returns no path, the fallback weights are applied (still strongly preferring lower LTS, but allowing higher LTS with steep penalty). The result is labeled in UI as **"Best-effort route — no fully safe path at this tier"** in distinct visual style.

**UI badge format**: Tier name + `(LTS allowance)` when first introduced on screen; tier name alone is acceptable in subsequent contexts. Example: `"Safe for kid (LTS 1)"` on the overview, just `"Safe for kid"` on the drill-down.

---

## 1. Product framing & user story

### 1.1 Product (one sentence)

A public web tool that turns a Chicago resident's home address and personal destinations into a printable, shareable advocacy artifact showing where bike infrastructure investment would most change their life.

### 1.2 Primary user persona

A Chicago resident or local bike advocate preparing to make a case to their alderman, neighborhood association, or community meeting. Not a tourist. Not a daily commuter looking for routing. Someone who wants to *argue for* infrastructure change with personal evidence.

### 1.3 Core user story

> *"I'm Jane. I live at 1234 W Foo St. I want to bike to my kid's school, Whole Foods, my pediatrician, and the alderman's office. At my chosen safety tier (Safe for kid), my routes detour 0.8 miles on average and cross 3 high-injury intersections. Fixing this one segment of Foster Ave would shorten my route to school by 6 minutes and reconnect 4 of my 7 destinations. Here's the link, alderman."*

### 1.4 Differentiator

The combination of personalized inputs (your home + your destinations) with structural analysis (LTS + HIN + gap segments) presented as a one-screen advocacy artifact. No existing tool does that combination.

### 1.5 Explicit non-goals

- NOT turn-by-turn bike navigation (Google Maps owns this; we'd be worse).
- NOT a citywide accessibility ranking artifact (PeopleForBikes owns this).
- NOT a real-time crash dashboard (Vision Zero / CMAP own this).
- NOT a community contribution platform in v1 (no user accounts, no submissions, no comments).
- NOT a multi-city tool in v1 (Chicago / Cook County only).

---

## 2. Interaction model

### 2.1 Overview view

Map-led, fills the viewport. Shows:

- User's home as a yellow pin (center).
- All destinations as category-icon pins.
- For each home→destination pair: fast route (dashed orange) + safe route (solid green).
- Where the two routes diverge: an **avoided intersection marker** highlighting what the safe route is going around. Markers weighted by impact (number of routes affected × log of total savings):
  - Top 1 → big red marker with alert badge.
  - Top 2–3 → smaller red markers with count badge.
  - Lower priority → small amber markers.
- Small legend top-right (route style key + marker key).
- Streets / Satellite basemap toggle top-left.
- Tier selector top-center: Safe for kid (LTS 1) / Safe for parent / Not safe.

**No always-visible side panel. No headline-ask popover. The map is the artifact.**

The four destination states the map must handle:
1. Big detour, clearly avoided intersection (the headline gap).
2. Smaller detour, smaller marker.
3. Safe route equals fast route (lines coincident, no marker — "you're fine" state).
4. No safe route exists at tier (fast route only, red "no safe route" badge on the destination pin).

### 2.2 Drill-down view

Triggered by clicking any route line, destination pin, or avoided-intersection marker on the overview. Slides into a detail view:

- Map zooms to fit the single home→destination pair, but never zooms further out than **zoom level 13** (~ 1 mile per inch). For long routes (e.g., 10+ mile cross-town pairs) where the full bounds don't fit at zoom 13, the map renders at zoom 13 centered on the route's midpoint and is **scrollable/pannable** along the route. A small "fit-route" button restores full-bounds view at lower zoom for spatial context.
- Other destinations hidden.
- Fast route (dashed orange) with high-stress segments outlined in red beneath.
- Safe route (solid green).
- Avoided intersection still the loudest visual element.
- Streets / Satellite toggle persists top-left.
- Legend persists top-right.
- **Fact panel slides in from the right (~340px wide)** containing:
  - Destination name and address.
  - Side-by-side fast vs. safe metrics (distance, time).
  - Detour cost (additional miles + minutes each way).
  - **Key gap callout**: the named segment or intersection that, if fixed, would shorten the safe route most. Tagged with HIN status if applicable. Linked to treatment options.
  - "Fast route crosses" list (HIN intersections, LTS-4 segments, etc.).
  - "Safe route uses" list (LTS-1 streets, neighborhood greenways, etc.).
  - **Copy permalink** button.
- Breadcrumb back to overview.

### 2.3 Permalink / sharing

State lives entirely in the URL hash fragment, encoded as `lz-string`-compressed JSON. Encodes home coordinates + destination set + safety tier + (optional) selected drill-down pair.

- Hash fragment never reaches the server (privacy bonus).
- Compressed payload fits 5+ destinations under ~250 chars.
- Round-trip property: copy URL → paste in fresh browser → reproduces exact same view.

**Sharing-foot-gun mitigation**: When a user clicks "Copy permalink," show a one-time confirmation modal:

> *"This link encodes your home address. Share it with people you trust — your alderman, your block club, your neighbors. Don't post it publicly (Twitter, Reddit, etc.) unless you want everyone to know where you live."*

The modal has two buttons:
- **Copy precise link** — full home coordinates encoded.
- **Copy approximate link** — encodes home as the centroid of its census block group (~1-3 block radius). Sacrifices some accuracy in the shown routes for shareability. Useful when posting to public forums for advocacy.

The choice is a per-copy decision; defaults remembered per session. Approximate links are visibly labeled in the UI when loaded ("Approximate home location — routes are illustrative") so the recipient knows the artifact is degraded.

### 2.4 Tier selection and POI input

User journey on first visit:
1. Enter home address (autocomplete via Nominatim).
2. Pick safety tier (default: Average).
3. Pick destination categories from a checklist (school, park, grocery, hospital, alderman office, library, CTA L). System auto-picks the nearest by crow-flies for each (rules in §3.6).
4. Optionally add free-form custom addresses (geocoded, treated as uncategorized destinations).
5. Map renders.

**No swap-destination picker in v1.** The auto-picked nearest is what you get.

### 2.5 LTS Data Explorer view

A third top-level view served at `GET /explore`, parallel to the advocacy flow at `/`. The Explorer shows the underlying bikemap data — every street colored by LTS, every intersection by `lts_approach`, with an optional High-Injury Network overlay — so advocates, planners, and journalists can inspect the raw infrastructure without going through the home/destination/route flow.

Discoverable via a small **"Explore LTS data →"** link in the top-right of the main advocacy view; a reciprocal **"← Back to advocacy view"** link sits in the Explorer.

The Explorer view has no advocacy UI: no tier selector, no home input, no destinations sidebar, no fact panel, no permalink modal. Its only encoded state is a `?hin=1` query string for the HIN overlay toggle.

Full design: see [`docs/superpowers/specs/2026-05-11-lts-data-explorer-design.md`](2026-05-11-lts-data-explorer-design.md). Implementation plan: Plan 2D (to be written).

---

## 3. Data architecture

### 3.1 Two-pipeline split

Heavy lifting offline. Web service is light and on-demand.

```
PREP PIPELINE (offline, monthly, runs on developer laptop)
─────────────────────────────────────────────────────────
  OSM (osmnx) ──┐
  CDOT Bike Facilities (sanity check) ──┤
  Chicago Speed Limit Zones ──┤── ► brokenspoke-analyzer ──► per-segment LTS
                              ┘
  CMAP 2025 SAP HIN ──► thin spatial-join mirror ──► hin_features
  Chicago Data Portal POIs ──┐
  OSM supermarkets ──────────┴► pois table
  treatments/*.md ──► treatments table
                              │
                              ▼
                          bikemap.db  (~150–250 MB)
                              │
                              ▼ (manual upload to Render persistent disk)

WEB SERVICE (Render Starter $7/mo)
──────────────────────────────────
  Flask backend
   ├── load streets + intersections into igraph at startup
   ├── /geocode  →  Nominatim (self-throttled)
   ├── /routes?home=…&dest=…&tier=…  →  A* on graph
   ├── /gap-analysis  →  job ID + cache lookup
   ├── /gap-analysis/status?job=…  →  status / result
   ├── /pois?near=…&category=…
   └── /treatments/:slug

  MapLibre GL JS frontend (no build step)
   ├── OpenFreeMap streets / Esri World Imagery satellite toggle
   ├── overview view + drill-down view
   └── permalink: lz-string-compressed JSON in URL hash fragment
```

### 3.2 Storage: SQLite + SpatiaLite

One `bikemap.db` file. Read-only in production. Tables:

| Table | Holds | Approx rows |
|-------|-------|-------------|
| `streets` | id, geom (WKB), osm tags, lts (1–3), length, on_hin, hin_modal_flags | 30k–100k |
| `intersections` | id, geom, lts_approach (1–3), on_hin, hin_modal_flags, hin_severity_rank | 20k–50k |
| `hin_features` | feature_id, kind (segment/intersection), modal_flags, severity, source_geom | ~10k |
| `pois` | id, geom, name, address, category, source, score (for default-pick ranking) | 10k–30k |
| `treatments` | slug, type, ward, location_geom, photo_path, source_url, summary, body_md | ~30 |
| `gap_cache` | hash(home, dest, tier), result_json, computed_at | grows over time, TTL = next refresh |
| `meta` | source, last_refresh, schema_version, record_count | one row per source |

**CRS**: EPSG:4326 for storage; EPSG:6454 (NAD83(2011) / Illinois East, metres) for distance math. (Earlier drafts referenced EPSG:3435, which is the same projection but in US survey feet — the metric variant is required for direct use of metre-denominated thresholds.)

### 3.3 Sources and treatment

| Source | Treatment | Conflict rule |
|--------|-----------|---------------|
| OpenStreetMap (osmnx) | **Source of truth** for streets, bike facilities, intersections | OSM wins |
| CDOT Bike Facilities | Sanity check only — discrepancies flagged in `prep_report.md` | OSM wins |
| Chicago Speed Limits | Supplements OSM `maxspeed` where missing | OSM wins where present |
| CMAP 2025 SAP HIN | Thin mirror: on-HIN flag, modal flags, severity rank | Stored separately, never blended into LTS or routing |
| `brokenspoke-analyzer` POI exports (schools, hospitals, parks, supermarkets, transit, etc.) | **Primary** for OSM-derivable POIs — comes free with the LTS run | brokenspoke wins where it covers the category |
| Chicago Data Portal POIs (alderman/ward offices, CPL library branches) | **Authoritative** for civic POIs that brokenspoke doesn't emit | CDP wins for these specifically |
| Nominatim | Address geocoding (live, server-side proxy) | n/a |

**POI source decision (v1-time evaluation)**: brokenspoke's exports include `neighborhood_schools`, `neighborhood_hospitals`, `neighborhood_parks`, `neighborhood_supermarkets`, `neighborhood_transit`, etc. — derived from OSM during the LTS run. If their quality is acceptable (verified during prep build, see §7.1), use them as the primary POI source for those categories and reserve CDP fetchers for **only what brokenspoke doesn't emit**: alderman/ward offices and CPL library branches. This significantly simplifies the fetcher pipeline. If brokenspoke's POIs prove inadequate (e.g., missing newer schools, miscategorized parks), fall back to CDP for those categories.

### 3.4 LTS engine and routing

- **LTS methodology**: Mineta LTS, per Mekuria, Furth & Nixon (2012, MTI Report 11-19) and Furth, Mekuria, Nixon (2016, TRR 2587). Both segment LTS and intersection approach LTS use these published rules. PFB City Ratings 2025 publishes 1-3 (collapsing original Mineta levels 3+4 into a single 'high stress' tier); we use that scale directly.
- **LTS engine**: `brokenspoke-analyzer` (PeopleForBikes open source, MIT-licensed) is the canonical executable implementation of the Mineta methodology. We run it locally in prep and consume its outputs directly. **We do not re-implement the Mineta thresholds ourselves.** The exact numerical rules (lane-count cutoffs, speed-limit cutoffs, intersection geometry handling) come from brokenspoke's source code, not from the spec.
- **Invocation pattern (verified from brokenspoke README)**:
  ```bash
  # all-in-Docker path
  export DATABASE_URL=postgresql://postgres:postgres@postgres:5432/postgres
  docker compose up -d
  docker run --rm --network brokenspoke-analyzer_default -e DATABASE_URL \
    ghcr.io/peopleforbikes/brokenspoke-analyzer:3.1.1 \
    -vv run --no-cache "united states" "chicago" "illinois" 1714000
  docker run --rm --network brokenspoke-analyzer_default \
    -u $(id -u):$(id -g) -v ./results:/usr/src/app/results -e DATABASE_URL \
    ghcr.io/peopleforbikes/brokenspoke-analyzer:3.1.1 \
    -vv export local "united states" "chicago" "illinois"
  ```
  Chicago FIPS code is `1714000` (Illinois state FIPS 17 + Chicago place FIPS 14000). Verify this exact value before first run.
- **Outputs (verified from brokenspoke `core/exporter.py`)**: results are emitted to `results/united-states/illinois/chicago/<version>/` as GeoJSON, Shapefile, and CSV files. Tables consumed by our prep pipeline:
  - `neighborhood_ways.geojson` → segment LTS → `streets` table
  - `neighborhood_ways_intersections.geojson` → intersection approach LTS → `intersections` table
  - POI tables (see §3.3) — usable as alternatives to CDP fetchers if quality permits.
- **Fallback**: `BikeOttawa/stressmodel` if brokenspoke produces unstable Chicago results — also Mineta-based, also open source, independently maintained.
- **Graph library**: `igraph` (C-backed, ~5–10× more memory-efficient than NetworkX).
- **Routing cost function** and **LTS weights**: see canonical definitions in §0.1; cost equation in §4.1.
- **Intersection LTS in routing**: per §4.1's max rule, an edge's effective LTS is `max(segment_lts, head_node.lts_approach)`. Both values come from brokenspoke output.
- **No HIN penalty in routing.** HIN is annotation only (see §4.3).
- **No-route-exists state**: when no in-tier path exists, fallback weights from §0.1 apply; UI shows "no safe route at this tier" badge and renders the best-effort route in distinct style.

### 3.5 Gap analysis with caching

For each home→destination pair:

- **Cache hit** on `(home_hash, dest_hash, tier)` → return instantly (sub-100ms).
- **Cache miss** → return immediately with a `job_id`; client polls `/gap-analysis/status?job=…` every 1.5s. UI shows a loading widget with step labels (*"Building safe route… computing gap candidates… ranking…"*). Average miss = 10–30 sec.
- Result cached for next user. **Address never persisted** — cache key is a coordinate hash (e.g., SHA-256 of rounded lat/long).
- Cache lives in a **separate writable SQLite file** (`cache.db`), not in `bikemap.db`. This keeps `bikemap.db` strictly read-only in production (resolves the read-only contradiction with the cache writer). `cache.db` is created on first cache write if it doesn't exist; it is *not* replaced during data refresh.
- **Cache reset on refresh**: when `make upload-db` deploys a new `bikemap.db`, the application also clears `cache.db` at next startup (it detects a different `bikemap.db` schema_version + record_count fingerprint than the cache was built against, then truncates).
- **LRU eviction**: when `cache.db` grows past **500 MB**, evict oldest entries until size drops below 400 MB. Run eviction asynchronously when the threshold is crossed; never block a user request on it.

Algorithm details in §4.5.

### 3.6 POI selection rules

Auto-pick the nearest by crow-flies per category. **No swap UI in v1.**

| Category | Default rule |
|---|---|
| School | Nearest CPS school |
| Park | Nearest park ≥ 0.5 acre |
| Grocery | Nearest OSM `shop=supermarket` |
| Hospital | Nearest hospital with ER |
| Alderman office | User's own ward office (geocode → ward → office) |
| Library | Nearest CPL branch |
| CTA | Nearest 'L' station |

User may also add free-form custom addresses (geocoded, treated as uncategorized destination, rendered with a generic destination pin distinct from category icons).

**Caveat for users**: the auto-picked nearest-by-crow-flies destination may not match the user's actual choice (e.g., the nearest CPS school may not be the school their kid attends; the nearest hospital may not be the one their pediatrician practices at). UI text near the destination list explicitly says: *"We picked the nearest by straight-line distance. If that's not the place you actually go, add it as a custom destination below."* This is a known v1 limitation; the swap-destination picker is deferred to v2 (§6.2).

### 3.7 Map services

| Role | Service |
|---|---|
| Streets basemap | OpenFreeMap |
| Satellite basemap | Esri World Imagery (or MapTiler free tier) |
| Geocoder | Nominatim, server-side proxy with self-throttling |
| Frontend renderer | MapLibre GL JS |

### 3.8 Privacy

- **No server-side address storage.** The server processes coordinates in memory to compute routes but never writes them to application logs or storage.
- **All endpoints accepting coordinates use POST request bodies, NOT URL query strings.** This is critical: query strings appear in our hosting provider's (Render's) reverse-proxy access logs, which we do not control. POST request bodies do not. Endpoints `/routes`, `/gap-analysis`, `/pois?near=…` accept coordinates as JSON in POST bodies.
- **Application logging middleware** additionally strips any incidental coordinate-bearing fields from log lines as defense in depth.
- **No analytics on inputs.** No third-party trackers in v1.
- **State lives entirely in the URL hash fragment** (`#…`) which never reaches the server. Encoded with `lz-string` compression.
- Gap-analysis cache key is a coordinate hash, not the raw address.
- **Geocoder proxy**: the `/geocode` endpoint proxies to Nominatim with our user-agent. Addresses are sent to Nominatim (necessarily) but not retained on our server. Privacy disclosure in the UI: *"Address autocomplete uses OpenStreetMap's Nominatim service. Your typed address is sent to OpenStreetMap.org for geocoding."*
- **Privacy verification**: launch criterion §6.4 includes a manual log-inspection check confirming no addresses or coordinates reach Render's access logs or our application logs.

### 3.9 Refresh

- **Monolithic monthly refresh**, triggered manually via developer reminder. `make refresh && make upload-db`.
- Each prep run produces `prep_report.md` with per-source `OK / WARN / FAIL`, record-count deltas, LTS regression vs. last run, and unmatched HIN feature list.
- All-or-nothing: any source fail → previous `bikemap.db` stays in place untouched.
- Last 3 source-snapshot directories kept on dev laptop for diff/rollback.

### 3.10 Web service ops

- **Hosting**: Render Web Service Starter ($7/mo). `bikemap.db` on Render persistent disk at `/var/data/bikemap.db` (read-only). `cache.db` lives at `/var/data/cache.db` (writable).
- **Startup validation**: `bikemap.db` exists, `schema_version` is compatible with code (per §3.11 append-only rule), sanity row counts (`streets >= 10000`). Fail → static "tool temporarily unavailable" page with last refresh date.
- **Database connections**: `bikemap.db` opened **read-only** by all routes. `cache.db` opened with a separate **read-write** connection used only by `app.core.cache`.
- **Graph load on startup**: streets + intersections loaded into igraph (~30-90s on Render Starter). To avoid health-check restart loops, the service exposes `/health` returning 503 until the graph is loaded, then 200; render.yaml uses `initialDelaySeconds: 120` for the health check (see §5.6). Optionally, prep ships a pickled igraph artifact alongside `bikemap.db` to cut load time to <10s — recommended optimization in v1 if startup time is observed > 60s.
- **Health check** every 30s after grace period; auto-restart on hard failure.
- **Rate limiting**: `slowapi` 60 req/min per IP via validated `X-Forwarded-For`. Gap cache absorbs scrape traffic.
- **Memory budget**: estimated 350–450 MB resident with igraph + Flask + read-only DB connection. Single-process Gunicorn (`-w 1 --threads 4` per §5.6) keeps total under Starter's 512 MB. Standard ($25) held as fallback only. Launch criterion (§6.4) verifies actual memory < 480 MB under 60 req/min synthetic load.

### 3.11 CI/CD and deployment

- **GitHub Actions** on push: `ruff` lint + `mypy` type-check + `pytest` (80% target on core logic — prep, routing, gap algorithm).
- **Render auto-deploys** main on green CI.
- **Code and data deploy independently**: `bikemap.db` is uploaded to Render Persistent Disk by developer after each prep run, *not* committed to git.
- **Schema versioning rule (append-only)**: `schema_version` is a monotonically increasing integer. Code MUST be backwards-compatible with the previous N=2 schema versions, so a code-only deploy never breaks against an old DB. When code drops support for a schema version, that bump requires a coordinated data refresh — documented as a release-notes item, not an automatic gate. This preserves "deploy code and data independently" as the normal case while keeping a clear escape hatch for breaking schema changes.
- **CRS reprojection** handled in prep pipeline; runtime never reprojects.

### 3.12 HIN-to-OSM spatial join (in prep pipeline)

- For each HIN segment: buffer-based join to OSM segments within 10m and within ±30° bearing. Unmatched features written to `hin_match_report.md` for manual review.
- For each HIN intersection: nearest-neighbor to OSM intersection nodes within 30m.
- HIN feature attributes stored on the matched OSM features as flags + ranks. **Never blended into LTS or routing.**

### 3.13 Testing strategy

- **Per-run regression report**: prep diffs this run's per-segment LTS scores vs. last run's. Unexpected churn surfaces in `prep_report.md`.
- Standard unit tests on routing logic, gap algorithm, POI selection rules, and HIN spatial join (covered by the 80% pytest target in §3.11).

### 3.14 Explicit non-goals (v1 scope cuts)

- **No mobile-specific layouts.** Desktop only. Phone use is best-effort viewport scaling.
- **No user accounts, submissions, or comments.** No backend write paths beyond `gap_cache`.
- **No multi-stop trip routing** (e.g., school → grocery → home as one chain). Each home→destination pair is independent.
- **No PDF / OG image / social-share artifact generation.** Permalinks only.
- **No swap-destination picker.** Auto-picked nearest-by-category is what you get.
- **No multi-city support.** Chicago / Cook County only.
- **No real-time updates.** Refreshed monthly, never live.
- **No automated cron-driven refresh.** Manual trigger via reminder.

---

## 4. LTS routing formalization

### 4.1 Cost function

Edge cost in meters.

**Fast route** — minimize bike-routable distance:
```
cost(edge) = length(edge)
```
Restricted to **bike-routable edges**, defined as OSM ways where:
- `bicycle != "no"` AND
- `access != "no"` AND
- `highway` not in {`motorway`, `motorway_link`, `trunk`, `trunk_link`} AND
- `highway` not in {`footway`, `pedestrian`, `steps`} *unless* `bicycle = "yes"` is explicitly set.

LTS and HIN ignored for the fast route.

**Safe route** — minimize stress-weighted distance:
```
effective_lts = max(edge.segment_lts, edge.head_node.lts_approach)
cost(edge)    = length(edge) × lts_weight(tier, effective_lts)
```

The **max rule** propagates intersection stress back into the routing decision: a calm side street (LTS 1) that ends at an unsignalized 4-lane arterial crossing (LTS 3 approach) is treated as LTS 3 for any traveler about to cross there. This matches the canonical Mineta interpretation — a route is only as safe as its scariest moment — and ensures the `lts_approach` data we compute and store actually drives routing decisions.

**No HIN penalty.** LTS (segment + intersection, via the max rule) is the only routing signal. Weights from §0.1.

### 4.2 LTS weights

Defined in §0.1. Routing module reads from a single config source so values cannot drift.

### 4.3 HIN's role — annotation only, never routing

HIN data does not influence which segments routing chooses. It is a parallel layer surfaced in the UI to reinforce the LTS story:

- **On the fast route**: *"Your fast route crosses 2 Cook County HIN intersections."*
- **On a gap segment**: *"This segment isn't just LTS 3 — it's also on the Cook County HIN (cyclist injuries: 8 since 2020). Strong advocacy case."*
- **On the safe route**: HIN crossings are noted if any (rare — usually the safe route avoids them by virtue of avoiding LTS > tier).

Modal flags drive the callout phrasing: *"on the cyclist HIN"* vs *"on the pedestrian HIN"* vs *"on the all-modes HIN."*

### 4.4 Best-effort fallback

When the safe-route function returns no path, fallback weights from §0.1 are applied. Result is labeled in the UI as **"Best-effort route — no fully safe path at this tier"** in distinct visual style.

**Visual treatment**: rendered as a **dashed amber line** (distinct from solid green safe route and dashed orange fast route). A `Best effort — no fully safe path` badge attaches to the route line. The destination pin gets a small warning indicator (yellow triangle) overlaid. The drill-down fact panel for that destination explicitly explains: *"No route at your safety tier exists. The route shown uses some streets above your tier — see segments highlighted in red beneath the line for the worst stretches."* The high-stress segments along the best-effort route (those above the user's tier) are outlined in red, just as on a fast route.

### 4.5 Gap analysis algorithm

**Inputs**: home, destination, tier, fast_route, safe_route.

**Cases:**
1. **safe_route is fallback** → no per-destination gap; flag destination "unreachable safely."
2. **safe_route == fast_route** → no gap; "no detour."
3. **Both exist, diverge** → run the algorithm.

**Algorithm (case 3):**

1. **Define the detour zone**: the polygon enclosing both `fast_route` and `safe_route`. Compute as the convex hull of the two routes' geometries, then buffer by **200 meters** outward. This zone captures candidates that lie *between* the two routes, not just on the fast route.
2. **Candidate set**: all edges and intersection nodes whose geometry intersects the detour zone, filtered by:
   - `feature_lts > tier_max_lts` (i.e., currently violates user's tier), AND
   - **Feasible-upgrade filter** — exclude features where infrastructure investment can't realistically lower LTS:
     - `highway` not in {`motorway`, `motorway_link`, `trunk`, `trunk_link`}
     - Not `railway`, `aerialway`, `waterway`
     - `access` not in {`private`, `military`, `no`}
3. **Candidate cap**: at most 100 candidates, sorted descending by `(feature_lts - tier_max_lts)` (largest violators first). Ties broken by feature length.
4. For each candidate:
   - For a **segment candidate**: hypothetically set its `segment_lts` to `tier_max_lts`.
   - For an **intersection candidate**: hypothetically set its `lts_approach` to `tier_max_lts`. (Per the §4.1 max rule, this lowers the effective_lts of every edge whose head is this intersection.)
   - Recompute safe_route on a *copy* of the graph (never mutate the shared graph in place).
   - Record `savings = current_safe_length - new_safe_length`.
5. Rank by savings descending. **Top 1 = headline gap.** Top 3–5 = supporting context in the drill-down fact panel.
6. **HIN annotation**: any candidate that is also on the HIN is tagged for stronger fact-panel framing — but HIN status doesn't change ranking.

**Why the detour-zone candidate set (vs. only "edges on fast_route"):** the real gap may be a segment neither on the fast nor current safe route — e.g., a missing connection 2 blocks east that, if upgraded, would let the safe route detour only 2 blocks instead of 4. Candidate sets restricted to the fast route would miss this case.

**Corridor detection (post-processing):**
- If the top-ranked candidate is adjacent (≤50m) to other top-5 candidates with savings ≥ 50% of the top, group them as a single corridor for display (e.g., *"Foster Ave between Western and Damen — 4 segments + 2 intersections"*).
- Implemented as a simple union-find pass over geographic adjacency.

**Performance bound:**
- 100 candidates × A* recompute ≈ 10–30 sec per fresh query → caching mandatory (per §3.5).

### 4.6 Multi-route aggregation (for overview view)

For the overview map's avoided-intersection markers:

1. Run gap analysis per home→destination pair.
2. Collect every avoided feature (segment or intersection) across all pairs.
3. Aggregate by `feature_id`:
   - `routes_affected` = count of destinations whose gap involves this feature.
   - `total_savings_meters` = sum of savings across affected routes.
4. Marker prominence ranking:
   ```
   priority = routes_affected × log(1 + total_savings_meters)
   ```
   Top 1 → big red marker. Top 2–3 → smaller red. Rest → amber. Single-route low-savings avoidances → drill-down only, not on overview.

### 4.7 Things explicitly NOT in the routing model

- HIN (never)
- Time-of-day variation
- Weather
- Elevation (Chicago is flat; not material)
- Turn penalties
- Multi-modal (bike + transit) routing

---

## 5. System architecture

### 5.1 Repo layout

The local checkout directory name is illustrative; the GitHub repo is `ZombieHunter386/Lakeview-Bike-Grid` (rename to `chicago-bike-advocacy-map` is recommended but optional — the layout below is what matters).

```
<repo-root>/
├── README.md
├── Makefile                       # entry points: refresh, upload-db, dev, test
├── pyproject.toml
├── render.yaml                    # Render service definition
├── docker/
│   └── Dockerfile                 # multi-stage; runtime stage stays slim
│
├── prep/                          # PREP PIPELINE — runs locally only
│   ├── main.py                    # orchestrates: fetchers → LTS → joins → DB
│   ├── fetchers/                  # one module per source
│   │   ├── osm.py
│   │   ├── cdot_sanity.py         # CDOT bike facilities — sanity check vs OSM
│   │   ├── speed_limits.py
│   │   ├── hin.py
│   │   ├── pois_cdp.py
│   │   └── pois_osm.py
│   ├── lts/
│   │   ├── runner.py              # invokes brokenspoke-analyzer
│   │   └── ingest.py              # consumes its output
│   ├── joins/
│   │   └── hin_to_osm.py          # buffer + bearing spatial join
│   ├── db/
│   │   ├── schema.sql
│   │   └── builder.py
│   ├── reporting/
│   │   ├── prep_report.py         # OK/WARN/FAIL + record-count deltas
│   │   └── lts_diff.py            # per-run regression diff
│   └── config/
│       └── sources.yaml           # source URLs, refresh metadata
│
├── app/                           # WEB SERVICE — runs on Render
│   ├── main.py                    # Flask entry
│   ├── routes/
│   │   ├── geocode.py             # → Nominatim
│   │   ├── routing.py             # /routes
│   │   ├── gap_analysis.py        # /gap-analysis + /status
│   │   ├── pois.py
│   │   └── treatments.py
│   ├── core/
│   │   ├── graph.py               # loads igraph from sqlite at startup
│   │   ├── routing.py             # A* with cost function
│   │   ├── gap_analysis.py        # gap algorithm + corridor detection
│   │   ├── poi_picker.py          # nearest-by-category logic
│   │   └── cache.py               # gap_cache read/write
│   └── static/                    # frontend (no build step in v1)
│       ├── index.html
│       ├── app.js                 # state, fetches, permalink encode/decode
│       ├── overview.js            # overview view (map + markers)
│       ├── drilldown.js           # drill-down view (map + fact panel)
│       └── styles.css
│
├── treatments/                    # markdown content (loaded into DB at prep)
│   ├── pedestrian-refuge.md
│   ├── protected-bike-crossing.md
│   ├── raised-intersection.md
│   ├── neighborhood-greenway.md
│   └── photos/
│
├── tests/
│   ├── prep/
│   │   ├── test_fetchers.py
│   │   ├── test_hin_join.py
│   │   └── test_lts_ingest.py
│   └── app/
│       ├── test_routing.py
│       ├── test_gap_analysis.py
│       └── test_poi_picker.py
│
├── docs/superpowers/
│   ├── specs/
│   │   └── 2026-05-04-chicago-bike-advocacy-map-design.md   ← this file
│   └── plans/
│
└── .github/workflows/
    └── ci.yml                     # ruff + mypy + pytest
```

**Module boundary rules:**
- `prep/` never imports `app/`. `app/` never imports `prep/`.
- `treatments/` is content, not code.
- Shared geometry helpers (if any) live in their own small module; resist the urge to share more than necessary.

### 5.2 Module responsibilities

| Module | Responsibility | Boundary |
|--------|---------------|----------|
| `prep.fetchers.*` | Pull source data → local cache files | One module per source. Each independently testable with a mock URL. |
| `prep.lts.runner` | Run brokenspoke-analyzer on cached OSM | Wraps a CLI tool; returns path to its output |
| `prep.lts.ingest` | Parse brokenspoke output → typed records | Pure data transformation |
| `prep.joins.hin_to_osm` | Spatial join HIN features → OSM features | Pure shapely/pyproj math |
| `prep.db.builder` | Populate `bikemap.db` from typed records | Single DB write entry point |
| `prep.reporting` | Emit `prep_report.md` and regression diff | Read-only consumers of prep output |
| `app.core.graph` | Load streets+intersections → in-memory igraph at startup | Owns igraph object; everything else asks it for routing |
| `app.core.routing` | A* over graph with cost function | Stateless functions; takes graph + tier + endpoints |
| `app.core.gap_analysis` | Run divergence algorithm + corridor detection | Pure function of graph + routes |
| `app.core.poi_picker` | Nearest-by-category lookup | Reads pois table; returns POI records |
| `app.core.cache` | Read/write `gap_cache` table | Read-only DB connection elsewhere; this is the only writer |
| `app.routes.*` | HTTP handlers | Thin layer; calls into `app.core.*` |
| `app.static/*.js` | Frontend state, MapLibre rendering, permalink codec | Plain ESM, no build step |

### 5.3 Deploy flow

```
[ developer laptop ]                      [ Render ]
                                          ┌──────────────────────┐
   git push ──────────────────────────────►  Render auto-deploy  │
                                          │  (code only)          │
                                          └──────────────────────┘

   make refresh                                    
     ↓ (fetches, runs brokenspoke,                 ┌──────────────────────┐
     produces bikemap.db locally,                  │  Render Persistent   │
     emits prep_report.md)                         │  Disk                │
     ↓                                             │  /var/data/          │
   review prep_report.md                           │    bikemap.db        │
     ↓                                             │                      │
   make upload-db ────────────────────────────────►│  (code reads from    │
     (Render API: PUT bikemap.db)                  │   here, never writes)│
                                                   └──────────────────────┘
```

Code and data deploy independently. A code-only push doesn't need new data; a data refresh doesn't need a code push. Web service restarts pick up new DB on next startup.

### 5.4 Frontend tooling

**No build step in v1.** Plain HTML + ESM JS modules + CSS. MapLibre via CDN. lz-string via CDN. If complexity grows in v2, switch to Vite then.

### 5.5 Key dependencies

**Backend (Python 3.11+):**
- `flask` — web framework
- `python-igraph` — graph + routing
- `shapely`, `pyproj` — geometry / projection (no full geopandas in runtime)
- `requests` — HTTP fetchers
- `slowapi` — rate limiting
- `gunicorn` — production WSGI server
- `pyyaml` — config files
- `python-frontmatter` — treatment markdown parsing

**Prep-only (not on Render):**
- `osmnx` — OSM extract
- `brokenspoke-analyzer` — LTS scoring
- `geopandas` — convenient for prep-time data wrangling

**Frontend (CDN):**
- `maplibre-gl-js`
- `lz-string`

**Dev:**
- `pytest`, `pytest-cov`
- `ruff`
- `mypy`

### 5.6 Render configuration (render.yaml sketch)

```yaml
services:
  - type: web
    name: chicago-bike-advocacy-map
    env: python
    plan: starter
    buildCommand: pip install -r requirements.txt
    startCommand: gunicorn -w 1 --threads 4 -b 0.0.0.0:$PORT app.main:app
    healthCheckPath: /health
    initialDelaySeconds: 120
    disk:
      name: bikemap-data
      mountPath: /var/data
      sizeGB: 1
    envVars:
      - key: BIKEMAP_DB_PATH
        value: /var/data/bikemap.db
      - key: CACHE_DB_PATH
        value: /var/data/cache.db
      - key: NOMINATIM_USER_AGENT
        value: chicago-bike-advocacy-map/1.0
```

**Why `-w 1 --threads 4`** (not `-w 2+`): each Gunicorn *worker* loads its own copy of the igraph in memory (~350-450 MB). Two workers would exceed Starter's 512 MB limit and OOM-crash. Threading is fine because routing is mostly NumPy/igraph C-level work that releases the GIL — multiple threads in one worker share the graph and serve concurrent requests well within memory budget.

**Why `initialDelaySeconds: 120`**: the graph load on startup takes 30-90s. Without the grace period, Render's default health checks would mark the service unhealthy and restart-loop it before the first request can be served.

### 5.7 Local development flow

```
make dev          # starts flask in debug mode against local bikemap.db
make refresh      # runs full prep pipeline → ./data/bikemap.db
make upload-db    # uploads ./data/bikemap.db to Render
make test         # ruff + mypy + pytest
make report       # opens prep_report.md from latest refresh
```

Local dev points at the same DB file the prod uses; no environment-specific schema.

---

## 6. V1 scope and launch criteria

### 6.1 In v1 (ship in 3–4 months)

**Core experience**
- Address-based personal advocacy artifact for all of Chicago / Cook County.
- Three safety tiers (see §0.1).
- Auto-picked categorized destinations (rules in §3.6) + free-form custom addresses.
- Three views: overview + drill-down (advocacy flow), and LTS data explorer at `/explore` (see §2 and the [explorer design doc](2026-05-11-lts-data-explorer-design.md)).
- Streets / Satellite basemap toggle.
- Permalink for sharing.
- Loading widget on first-time gap queries.

**Data**
- LTS via `brokenspoke-analyzer` on OSM.
- CMAP 2025 SAP HIN as annotation layer.
- POIs from Chicago Data Portal + OSM (groceries).
- Treatment library: 5–10 markdown entries with photos and Chicago examples.
- Monthly manual refresh with `prep_report.md` + LTS regression diff.

**Infrastructure**
- Render Starter ($7/mo) + persistent disk (~$0.25/mo).
- SQLite + SpatiaLite, single `bikemap.db`.
- Nominatim geocoding (self-throttled).
- OpenFreeMap streets / Esri or MapTiler satellite.
- Per-IP rate limiting (60 req/min).
- No address logging, no analytics on inputs.

### 6.2 Deferred to v2 (or beyond)

**UX**
- Mobile-specific layouts (drawer, gesture handling, responsive panels).
- Swap-destination picker.
- Multi-stop trip routing.
- PDF / OG image / social-share artifact generation.
- Embeddable widgets for advocacy organizations.
- Citywide aggregate / heatmap view.

**Routing**
- Time-of-day variation.
- Weather, elevation, turn penalties.
- Multi-modal (bike + transit).
- Cache warming for popular addresses.

**Data**
- Crowdsourcing / aggregating user-submitted addresses.
- Real-time updates.
- Multi-city support beyond Chicago / Cook County.
- Postgres + PostGIS migration.
- Automated Cron-driven refresh.

**Infrastructure**
- Cloudflare / authenticated quotas.
- User accounts / submissions / comments.

### 6.3 Explicit never-goals

- Turn-by-turn bike navigation.
- Citywide accessibility ranking artifact.
- Real-time crash dashboard.
- For-profit features / paid tiers.

### 6.4 Launch criteria

V1 is not ready to ship until **all** of these are true. None can be waived.

1. **LTS sanity** — `brokenspoke-analyzer` produces plausible scores on a hand-checked sample of Chicago streets (Milwaukee, Lincoln, Western, lakefront trail, residential side streets in three neighborhoods). No gross misclassifications.
2. **HIN join coverage** — ≥ 95% of HIN features successfully joined to OSM features. Unmatched features documented in `hin_match_report.md` with manual review.
3. **End-to-end deploy** — code push to main → green CI → Render auto-deploy → live at staging URL → returns valid routes for 5+ test addresses.
4. **Routing reasonableness** — 10 hand-tested home addresses across diverse neighborhoods (Lakeview, Albany Park, Bronzeville, Pilsen, Auburn Gresham). Each tier produces routes that "look right." No obvious failures.
5. **Gap analysis quality** — same 10 addresses produce gap callouts that name actual known-bad infrastructure. If the algorithm flags a calm side street as "the gap," we don't ship.
6. **Permalink round-trip** — copy URL → paste in fresh browser → reproduces exact same view. Includes free-form addresses.
7. **Privacy verified** — server logs inspected after a test session: no addresses, no coordinates, no identifying input data captured.
8. **Performance floor**:
   - Overview page initial HTML/JS loads under 3s on a normal home connection.
   - **First-visit time to first usable map** (any one destination's routes drawn) under 10s on a fresh address.
   - **Full overview rendered** (all 7 categorized destinations + their gaps) under 90s on a fresh address. Gap analyses run in parallel (cap 3 concurrent); destinations render incrementally as each completes — never gated on the slowest.
   - **Cached gap query** returns under 200ms.
9. **Memory budget verified** — `psutil`-measured resident memory under 480 MB while serving a synthetic 60 req/min load over 5 minutes.
10. **No build-time errors or warnings** — `make test` passes clean. `prep_report.md` shows all sources OK.

### 6.5 Rollout plan

1. **Soft launch** — share with 3–5 trusted Chicago bike advocates. Collect feedback. Fix obvious bugs. ~2 weeks.
2. **Targeted announce** — email Active Trans, post in Chicago bike advocacy lists, share on Streetsblog Chicago. Aim for 100–500 first-week users.
3. **General public** — Twitter/Bluesky, Streetsblog feature, neighborhood newsletters.

No PR push or paid promotion in v1. Word of mouth + bike advocacy networks are the channel.

---

## 7. Open research items

These are flagged for verification during the prep-pipeline build phase. None block locking the spec, but each must be resolved before the corresponding part of the build proceeds.

### 7.1 Blocking before prep build starts

1. **CMAP 2025 SAP HIN — exact ArcGIS REST endpoint.** Confirmed publicly accessible via Cook Central GIS Hub. Exact REST URLs for the 2025 SAP layers (segments + intersections + modal flags) need pinning.
2. **`brokenspoke-analyzer` output column schema.** Output *table* names are confirmed (verified in `core/exporter.py`): `neighborhood_ways.geojson` for segments, `neighborhood_ways_intersections.geojson` for intersections, plus POI exports per §3.3. Still need to verify per-property field names within those GeoJSON layers — specifically:
   - (a) Which property holds the LTS integer? (likely `lts`, possibly `tf_lts`/`ft_lts` for to-from / from-to direction encoding)
   - (b) LTS scale: PFB 2025 publishes 1-3 (collapsing original Mineta 3+4 into one high-stress tier). Confirmed; we use 1-3.
   - (c) Is intersection LTS per-approach-direction or aggregated to one value per node? Critical for the §4.1 max rule (directional matters).
   - (d) Which OSM tags brokenspoke consumes for intersection scoring (signal type, lane counts, channelized turns), so we can validate Chicago OSM coverage of those tags is adequate.

   Record findings in `prep/lts/ingest.py` docstring. This documentation is also the authoritative reference for our methodology — we defer to brokenspoke's implementation rather than restating Mineta thresholds in our own code.

2a. **brokenspoke POI quality check.** Run brokenspoke on a small Chicago bounding box and inspect each POI export (`neighborhood_schools`, `neighborhood_hospitals`, `neighborhood_parks`, `neighborhood_supermarkets`, `neighborhood_transit`). For each: spot-check 10 features against ground truth (Google Maps, CDP datasets). Decide per-category whether brokenspoke's export is acceptable as primary source (per §3.3) or whether we need CDP fallback. Drives whether `prep/fetchers/pois_cdp.py` covers all civic POI categories or only alderman + library.
3. **POI category coverage on Chicago Data Portal.** Confirm authoritative datasets exist for: CPS schools, parks (with acreage), hospitals (with ER flag), alderman office locations, CPL branches, CTA 'L' stations.

### 7.2 Important to resolve early in prep build

4. **HIN-to-OSM spatial join unmatched rate.** Run buffer-based join on full Chicago. If unmatched rate > 5%, refine methodology.
5. **OSM bike infrastructure validation.** Spot-compare 30 random OSM-tagged bike facilities against CDOT's published data. Document discrepancy rate.
6. **Chicago Speed Limit Zones — street-level coverage.** Verify it covers residential / collector streets, not just arterials. Fall back to OSM `maxspeed` defaults where missing.
7. **`brokenspoke-analyzer` Chicago feasibility / runtime.** Verify it completes without errors, runtime under 4 hours on typical laptop, LTS distribution looks plausible. If unstable: fall back to `BikeOttawa/stressmodel`.

### 7.3 Nice-to-have / can defer to launch prep

8. **Render Persistent Disk exact pricing and upload mechanism.**
9. **Nominatim TOS** for our usage profile. If we exceed public-API limits, options: self-host or paid geocoder fallback.
10. **OpenFreeMap Chicago tile coverage and uptime.** Spot-check completeness and historical reliability before committing.
11. **Satellite tile provider choice.** Esri World Imagery vs. MapTiler free tier.
12. **Chicago 2023 ward boundaries — current dataset.** Verify Data Portal's ward shapefile is the post-2023-redistricting version.
13. **PeopleForBikes outreach result.** Pending email reply to `grace@peopleforbikes.org`. If they share Chicago LTS data, cross-validate our brokenspoke output as bonus credibility.

---

## 8. Document control

- **Section 0.1 is the canonical source for all tier definitions.** Other sections reference it. Do not duplicate.
- All routing weights and tier rules in code MUST read from a single config source (e.g., `prep/config/routing_weights.yaml`) sourced from §0.1. Drift between code and spec must be impossible.
- This document is the design spec. The implementation plan (next document, named `docs/superpowers/plans/YYYY-MM-DD-chicago-bike-advocacy-map-plan.md` and dated when written) will translate it into ordered build steps with acceptance criteria.
