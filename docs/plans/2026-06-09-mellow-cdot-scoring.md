# Chicago Bike Grid — Plan: Mellow + CDOT scoring swap

> **For agentic workers:** implement task-by-task with TDD (RED → GREEN → REFACTOR). Steps use checkbox (`- [ ]`) syntax. Spec: [`docs/specs/2026-06-09-mellow-cdot-scoring-design.md`](../specs/2026-06-09-mellow-cdot-scoring-design.md).

**Goal:** Replace the brokenspoke/PFB LTS source with a stress classification built from the Mellow Bike Map (baseline) + CDOT bike-facility layers (override), on a routing graph built from OpenStreetMap. The 3-tier model, weight tables, router, HIN overlay, POIs, and both views are unchanged.

**Tech stack delta:** add `osmnx` (graph build) + keep `geopandas`/`shapely`/`pyproj`. Remove Docker/brokenspoke. `pytest` + `responses` for tests as today.

**Working state at end of plan:** `make refresh` (no Docker) produces a valid `data/bikemap.db` with `streets.lts` / `intersections.lts_approach` populated from Mellow + CDOT, plus `lts-network.geojson.gz`. `make test` passes clean.

---

## Phase 0 — Setup

- [x] **0a. Worktree.** Created `feat/mellow-cdot-scoring` at `.worktrees/mellow-cdot-scoring`; baseline 176 tests pass.
- [x] **0b. Dependency.** `osmnx>=2.0,<3` already in `requirements.txt`; verified `import osmnx` (2.1.0) in the venv.
- [x] **0c. Discover CDOT endpoints.** Done — see `docs/dataset-ids.md`. Chosen layers: on-street `Bikeway_Network_2024_Final_Public` (field `BIKE_DSPLY`), off-street `Trails_Network_2024_11_18`; both geometry-enabled at `outSR=4326`. Live vocab differs from the original draft (abbreviated single words; no "Signed Bike Route") — §1.1 updated accordingly.

## Phase 1 — Pure classifier (no I/O, fully unit-tested)

- [ ] **1a. RED:** `tests/prep/test_tier_classifier.py` — truth table for a new `prep/scoring/classifier.py::classify_tier(mellow_kind, cdot_facility)`. Cases: each Mellow kind alone; each CDOT facility alone; both-agree; both-disagree (CDOT wins); Mellow **path** + worse CDOT (stays tier 1 — the floor); neither (tier 3).
- [ ] **1b. GREEN:** implement `classify_tier` + the `CDOT_FACILITY_TO_TIER` and `MELLOW_KIND_TO_TIER` tables per design §1.1. **Key the CDOT table on the live `BIKE_DSPLY` vocab** (`PROTECTED`/`NEIGHBORHOOD`→1, `BUFFERED`/`BIKE`→2, `SHARED`→3) — not the title-case strings; see §1.1 / `docs/dataset-ids.md`. Keep it a pure function over strings — no geometry.
- [ ] **1c. REFACTOR:** move the facility/kind vocab into module constants; assert exhaustiveness (unknown facility string → baseline, logged). Match case-insensitively / normalize whitespace so the fallback `DISPLAYROU` vocab (`PROTECTED BIKE LANE`, …) also resolves if that layer is ever swapped back in.

## Phase 2 — Source parsers

- [ ] **2a. Mellow fetch + parse.** RED: `tests/prep/test_mellow_fetcher.py` with a small fixture mirroring a Django `mbm.mellowroute` dumpdata record (fields incl. `type`/kind + geometry/ways). GREEN: `prep/fetchers/mellow.py` downloads the fixture file(s) from `jeancochrane/mellow-bike-map` `app/mbm/fixtures/` and yields `MellowFeature(kind, geometry)`. Reproject to EPSG:4326. **Verification:** parses route/street/path into the three kinds.
- [ ] **2b. CDOT facilities fetch + parse.** RED: `tests/prep/test_cdot_facilities_fetcher.py` using a saved FeatureServer JSON fixture. GREEN: `prep/fetchers/cdot_facilities.py` (ArcGIS query, `outSR=4326`, paginated) yields `CdotFacility(facility_type, geometry)` for on-street + off-street trail layers. Mirror the existing `prep/fetchers/hin.py` ArcGIS pattern. **Verification:** facility-type strings map through `CDOT_FACILITY_TO_TIER`.

## Phase 3 — OSM graph builder

- [ ] **3a. RED:** `tests/prep/test_osm_graph.py` — feed a tiny saved OSM graph (osmnx graphml fixture or a hand-built MultiDiGraph) and assert `build_street_edges(graph)` yields edge records with: stable `head_node_id`/`tail_node_id`, LineString geom (EPSG:4326), `length_m`, `highway`, `name`. Assert the graph is connected.
- [ ] **3b. GREEN:** `prep/graph/osm_builder.py` — `osmnx.graph_from_bbox(...)`, simplify, project to 4326, emit edge + node records. **Emit the existing `prep.lts.ingest.SegmentRecord` / `IntersectionRecord` dataclasses** so `DbBuilder.insert_streets` / `insert_intersections` work unchanged (review F5; builder computes `length_m` itself). Synthesize a stable unique int `road_id` per edge; this `road_id` is also the HIN match key (builder.py:75, main.py:361). Leave `ft_int_str`/`tf_int_str` = `None` (intersection tiers come from Phase 4c). **Gotchas (review):** (F2) osmnx 2.x `graph_from_bbox(bbox, *, …)` takes a single tuple ordered `(left, bottom, right, top)` = `(min_lng, min_lat, max_lng, max_lat)` — `target.bbox` is `(min_lat, max_lat, min_lng, max_lng)`, so **reorder before the call**. (F3) `streets.osm_id` is `INTEGER NOT NULL` but simplified osmnx edges carry a **list** of `osmid`s — collapse to a single int (first element). Use osmnx `u`/`v` node ids as `head_int_id`/`tail_int_id`. **Verification:** node IDs at edge ends are consistent (shared between adjacent edges) so the router can traverse.

## Phase 4 — Spatial classify (graph × sources)

- [ ] **4a. RED:** `tests/prep/test_classify_network.py` — given OSM edges + Mellow features + CDOT facilities (all fixtures), assert each edge gets the correct tier via buffer-match (reuse the `prep/joins/hin_to_osm.py` matching approach). Include the Mellow-path-floor case end-to-end.
- [ ] **4b. GREEN:** `prep/scoring/classify_network.py` — for each edge: nearest/overlapping Mellow feature → baseline; nearest/overlapping CDOT facility → override via `classify_tier`; apply path floor. Attach `lts` to each edge record. **(review F7)** The reused `hin_to_osm` matcher applies a ±30° bearing filter — right for on-street CDOT lines, but **off-street trails/paths may not parallel street bearing**, so use a bearing-optional match for the trail layer (off-street → tier 1 regardless, which mostly sidesteps it).
- [ ] **4c. Intersections.** RED+GREEN: `prep/scoring/intersection_tiers.py::lts_approach_for_node` = max incident edge tier. Replaces `synthesize_intersections` for the new graph. **(review F6)** Preserve the existing floor: a node with no incident-edge tiers defaults to `lts_approach = 1` (schema is NOT NULL). Node geometry comes from osmnx node coords. **Verification:** node tier = worst incident edge.

## Phase 5 — Pipeline wiring

- [ ] **5a. sources.yaml + config_loader.** The `mellow`, `cdot_bike_network`, `cdot_off_street_trails` entries already exist (Phase 0). Remove the `brokenspoke:` block **and** drop `prep/config_loader.py`'s required-`brokenspoke` section (config_loader.py:60 raises if absent — review F4); remove `BrokenspokeConfig` from the dataclass + loader. Update `tests/prep/test_config_loader.py`.
- [ ] **5b. main.py (restructure, not a 2-line swap — review F4).** RED: extend `tests/prep/test_main.py`. GREEN: the whole streets/intersections/HIN/POI insert block is nested under `if results_path is not None:` (main.py:343) — replace that gate with the new flow: fetch Mellow + CDOT → `build_street_edges` → `classify_network` → `intersection_tiers`. Drop the step-2 brokenspoke runner and `--skip-brokenspoke`. Keep the HIN join (pass each edge's synthesized `road_id` as `OsmSegment.osm_id`, exactly as today), POI ingest, treatments, atomic swap, `lts-network` export.
- [ ] **5c. Delete dead code.** Remove `prep/lts/runner.py`, `docker/compose.brokenspoke.yml`, brokenspoke refs; trim `prep/lts/ingest.py` to the POI helpers only (drop `ingest_segments*`, `SegmentRecord`/`IntersectionRecord` move to wherever Phase 3 emits them). Update imports.
- [ ] **5d. Reporting.** Keep `prep/reporting/lts_diff.py` (still diffs `streets.lts`); retitle output to reflect Mellow/CDOT. Update `prep/reporting/prep_report.py` source list. Decide on `cdot_sanity` (Socrata `hvv9-38ut`) fetcher: keep as cross-check or remove (review F8).
- [ ] **5e. POI parity (review F1 — user decision 2026-06-09: keep all categories).** Removing brokenspoke removes the *input* to `ingest_brokenspoke_pois` (8 categories: pharmacy, doctor, dentist, university, college, community_center, social_services, retail — not covered by the current OSM POI fetcher). Expand `prep/fetchers/pois_osm.py` + `OSM_POI_FILES` to fetch those 8 from OSM (same data brokenspoke repackaged), then drop `ingest_brokenspoke_pois`. RED: extend `tests/prep/test_pois_osm_fetcher.py`. **Verification:** all 13 POI categories present in the built DB.

## Phase 6 — Verify & integrate

- [ ] **6a.** `make test` green (ruff + mypy + pytest). Fix any app/router/test regressions (schema is unchanged, so these should pass untouched).
- [ ] **6b.** Small-bbox integration build (e.g. a few sq-mi around a Mellow-mapped neighborhood): run `prep/main.py` against the mini bbox, assert DB builds, `streets.lts` distribution is plausible (not all tier 3), graph connected, `/explore` GeoJSON exports.
- [ ] **6c.** Full Chicago `make refresh`; eyeball `prep_report.md` + the `/explore` map: protected lanes/greenways render green, mellow streets amber, everything else red. Spot-check 3–4 known protected-lane streets (e.g. a known PBL corridor) read tier 1.
- [ ] **6d.** README: replace Docker/brokenspoke setup with the osmnx flow.
- [ ] **6e.** Review against this plan + design; deploy note for Railway (no Docker-in-prep means simpler build).

## Notes / risks

- **Mellow coverage is partial** (volunteer-mapped, North Side strongest). Streets outside Mellow + CDOT fall to tier 3 by default — expected, but verify it doesn't make routing infeasible in under-mapped areas. The tier *fallback* weights in `routing_weights.yaml` already soften hard cutoffs.
- **Graph keying** is the riskiest change: the HIN join and `lts_diff` both key off `road_id`. **Validated in review:** `DbBuilder.insert_streets` looks up HIN matches by `road_id` (builder.py:75) and `main.py:361` passes `road_id` as `OsmSegment.osm_id`. Phase 3 must synthesize a stable unique int `road_id` per edge and Phase 5b keep that exact wiring; lean on `test_hin_to_osm.py` + `test_lts_diff.py`.
- **Licensing:** Mellow is MIT; CDOT data is public. Add attribution to the README/footer.

## Plan review addenda (2026-06-09, against live code)

Reviewed before Phase 1. Verdict: plan sound; tier direction confirmed (1 = kid-safe, `routing_weights.yaml`), schema/weights untouched. Findings folded into tasks above: **F1** POI parity → new task 5e (keep all 13 categories, user decision); **F2** osmnx 2.x bbox order → 3b; **F3** `osm_id` list-collapse → 3b; **F4** main.py restructure + config_loader `brokenspoke` coupling → 5a/5b; **F5** emit existing `SegmentRecord`/`IntersectionRecord` → 3b; **F6** intersection floor=1 → 4c; **F7** bearing-optional trail match → 4b; **F8** `cdot_sanity` keep/remove → 5d.
