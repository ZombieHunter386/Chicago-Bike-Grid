# Cook County LTS 2023 (4-level) scoring — Design

**Date**: 2026-07-29
**Status**: APPROVED by user 2026-07-29 (data source, 4 personas, road-class fallback)
**Scope**: Replaces the segment-stress *source* and moves the whole app from a
3-tier stress scale to the standard 4-level LTS scale. The Mellow Bike Map +
CDOT classifier (2026-06-09 design) is removed entirely; street stress now
comes from Cook County DoTH's published Level of Traffic Stress (2023) layer,
joined to our OSM graph by way ID. The routing graph build (osmnx), HIN
overlay, POI layers, treatments, and the overall shape of `/` and `/explore`
are unchanged.

---

## 1. Purpose & locked decisions

Cook County DoTH publishes an LTS rating for every roadway segment in the
Chicago metro area, computed with the University of Minnesota Accessibility
Observatory methodology over 2023 OSM data (road class, speed, lane
configuration, bike facilities). It is the real, agency-published version of
what our hand-rolled Mellow+CDOT classifier approximated — and it uses the
standard 4-level LTS scale (1 = least stress, 4 = most).

Decisions locked with the user (2026-07-29):

1. **Full replacement.** Cook County LTS is the only stress source. Mellow +
   CDOT fetchers, their `sources.yaml` entries, and the current classifier
   logic are removed. Known trade-off: the layer is a 2023 snapshot updated
   annually, so facilities built since then won't show until the county
   refreshes it.
2. **Four personas** replace the three route tiers: **Kid** (LTS 1),
   **Inexperienced** (LTS 1–2), **Experienced** (LTS 1–3), **Death wish**
   (LTS 1–4). Old `parent`/`any` tier keys are removed (frontend is the only
   API consumer; no back-compat shim).
3. **Unmatched edges → road-class baseline**, extended to 4 levels (§3.2), not
   a blanket worst-case LTS 4.

## 2. Data source

| | |
|---|---|
| Dataset | Level Of Traffic Stress (2023), Cook County DoTH "DOTH_expanded" service |
| Layer URL | `https://gis.cookcountyil.gov/traditional/rest/services/DOTH_expanded/MapServer/14` |
| Hub page | `https://hub-cookcountyil.opendata.arcgis.com/datasets/cookcountyil::level-of-traffic-stress-2023` |
| Records | 207,459 polylines (verified 2026-07-29): LTS 1 = 153,880 · 2 = 2,858 · 3 = 10,985 · 4 = 39,736 |
| Fields used | `way_id` (esriFieldTypeDouble — the OSM way ID), `lts` (string `"1"`–`"4"`) |
| Pagination | `maxRecordCount` 2000, `supportsPagination: true` |
| Refresh | annual (county-stated) |

Key property: because the county computed LTS *from OSM*, `way_id` is a real
OSM way ID (spot-verified 2026-07-29: 24072568 = North Marmora Avenue). So the
join to our osmnx edges is an exact **way-ID join** — the same pattern the
Mellow join used — with no spatial matching and no geometry download at all.

**Fetcher** (`prep/fetchers/cook_lts.py`): pages through the layer with
attribute-only queries (`returnGeometry=false`, `outFields=way_id,lts`,
`resultOffset` pagination, ~104 pages), writes one JSON snapshot
(`cook_lts.json`) into the day's snapshot dir, and reports record count +
status like every other fetcher. A record count far below ~200k or an
unparseable `lts` value → WARN/FAIL per the existing fetcher conventions.
Record the layer in `docs/dataset-ids.md` alongside the discovery notes.

`sources.yaml`: add `cook_lts` block (type `arcgis_mapserver_layer`, url,
refresh cadence); delete `mellow`, `cdot_bike_network`,
`cdot_off_street_trails` blocks. `hin`, `chicago_speed_limits`, CDP + OSM POI
sources unchanged.

## 3. Classification algorithm

Replaces `prep/scoring/classifier.py`'s three-source precedence
(CDOT > Mellow > road class) with a two-step rule:

### 3.1 Way-ID join (primary)

- Build `way_id -> lts` (int 1–4) from the snapshot. Duplicate `way_id` rows
  (the county splits some ways) collapse to the **worst (max)** LTS.
- Each osmnx edge carries one or more OSM way IDs (simplified edges carry a
  list). Edge LTS = **worst (max)** LTS over its matched way IDs. Worst-wins
  is deliberate — a segment is as stressful as its worst stretch. (The Mellow
  join used best-wins, but that was choosing a route *kind*; this is a safety
  rating.)

### 3.2 Road-class baseline (fallback, unmatched edges)

OSM ways created or renumbered since the 2023 snapshot won't match. They fall
back to the OSM `highway` class, extending the existing `ROAD_CLASS_TO_TIER`
table to 4 levels:

| LTS | `highway` values |
|---|---|
| 1 | `residential`, `living_street`, `cycleway`, `path`, `footway`, `pedestrian` |
| 2 | `track`, `unclassified`, `tertiary`, `tertiary_link` |
| 3 | `secondary`, `secondary_link` |
| 4 | `primary`, `primary_link`, `trunk`, `trunk_link`, `motorway`, `motorway_link`, `busway`, unknown/missing |

### 3.3 Intersections

Unchanged rule (`lts_approach` = worst incident edge, floor 1 for isolated
nodes), now producing values 1–4.

### 3.4 Reporting

`prep_report.md` gains a **match-rate** line: edges matched by way ID vs.
edges on the road-class fallback (count + %), so 2023→now way-ID drift is
visible every run. `lts_diff.py` works unchanged (it diffs `streets.lts`
integers).

## 4. Personas & routing weights

`app/core/weights.py` + `prep/config/routing_weights.yaml` (canonical) move to
four tiers × four LTS levels. `INF_WEIGHT = 1e9` sentinel and the
any-edge≥INF "no in-tier path" detection are unchanged.

| Tier key | Label (UI) | Allowed | Main weights | Fallback weights |
|---|---|---|---|---|
| `kid` | Safe for kid · LTS 1 | 1 | [1.0, ∞, ∞, ∞] | [1.0, 5.0, 20.0, 40.0] |
| `inexperienced` | Inexperienced · LTS 1–2 | 1–2 | [1.0, 1.2, ∞, ∞] | [1.0, 1.2, 10.0, 20.0] |
| `experienced` | Experienced · LTS 1–3 | 1–3 | [1.0, 1.2, 1.5, ∞] | [1.0, 1.2, 1.5, 10.0] |
| `death_wish` | Death wish · LTS 1–4 | 1–4 | [1.0, 1.2, 1.5, 2.0] | [1.0, 1.2, 1.5, 2.0] |

`_validate_lts` accepts 1–4. Routing API `tier` parameter accepts exactly
these four keys; `parent` and `any` are removed everywhere (routes, frontend
`state.js`/`api.js`, tests).

## 5. UI

- **`/` tier buttons** (`index.html`): four buttons — "Safe for kid (LTS 1)",
  "Inexperienced (LTS 1–2)", "Experienced (LTS 1–3)", "Death wish (LTS 1–4)";
  default active = `death_wish` (mirrors today's `any` default).
- **Color ramp**, everywhere LTS is drawn (`overview.js` route split colors,
  `explore.js` network ramp, legend swatches in `index.html`/`explore.html`,
  `styles.css` `rl-lts-*` classes): LTS 1 `#16a34a` green · LTS 2 `#eab308`
  yellow · LTS 3 `#f59e0b` orange · LTS 4 `#dc2626` red.
- Legend text updates from "LTS 1-3" to the four-level labels.

## 6. Schema & plumbing

- `streets.lts`, `intersections.lts_approach`: stay `INTEGER NOT NULL`; range
  comments update 1..3 → 1..4.
- `app/core/graph.py`: int8 arrays and `eff_lts = max(seg, head)` logic are
  value-agnostic; comment updates only.
- Dead code removed: `prep/fetchers/mellow.py`, `prep/fetchers/cdot_facilities.py`,
  Mellow/CDOT branches of `prep/scoring/classifier.py` +
  `classify_network.py`, their tests, and the fetch/parse/classify wiring in
  `prep/main.py` (replaced by the cook_lts fetch + new classify call).
- `prep/reporting/` gains the match-rate summary (§3.4).

## 7. Testing (TDD)

- Fetcher: pagination loop, snapshot shape, WARN/FAIL thresholds (mocked HTTP).
- Classifier truth table: single-way match each LTS 1–4; multi-way edge
  worst-wins; duplicate way_id worst-wins; unmatched → each road-class level;
  string→int parsing (incl. bad values → fallback + warning).
- Weights: 4×4 table sanity, `_validate_lts` bounds, INF placement per tier.
- Intersections: worst-incident rule over 1–4.
- Routing: tier-key rename (four keys valid, old keys 4xx), fallback-weight
  path when main is all-INF.
- Frontend/API contract tests updated for four tiers.
- Integration: small-bbox end-to-end build → `streets.lts` ⊆ {1,2,3,4}, sane
  distribution, graph connected, match rate reported.
- Regression: `lts_diff` between a 3-tier and 4-tier DB still renders (it
  reports integer transitions).

## 8. Rollout

1. Merge to `main` after review; Railway auto-deploys the app image from the
   Dockerfile (no schema migration — new DB is built offline).
2. Run `make refresh` locally (~30–90 min) to build the 4-level
   `data/bikemap.db` + `lts-network.geojson.gz`; inspect `prep_report.md`
   (match rate) and eyeball `/` + `/explore` locally.
3. Upload the new DB artifacts with the existing upload flow
   (`prep/upload_db.py`), using the Railway API token supplied 2026-07-29.
4. Verify production `/` routes with all four personas and `/explore` shows
   the 4-color ramp.

## 9. Out of scope

- Per-direction LTS; time-of-day stress (unchanged from prior design).
- Supplementing the 2023 snapshot with newer CDOT facilities (explicitly
  rejected in favor of full replacement; revisit when the county ships 2024+).
- Renaming DB columns or API field names beyond the tier keys.
