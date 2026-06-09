# Chicago Bike Grid — Plan: Mellow + CDOT scoring swap

> **For agentic workers:** implement task-by-task with TDD (RED → GREEN → REFACTOR). Steps use checkbox (`- [ ]`) syntax. Spec: [`docs/specs/2026-06-09-mellow-cdot-scoring-design.md`](../specs/2026-06-09-mellow-cdot-scoring-design.md).

**Goal:** Replace the brokenspoke/PFB LTS source with a stress classification built from the Mellow Bike Map (baseline) + CDOT bike-facility layers (override), on a routing graph built from OpenStreetMap. The 3-tier model, weight tables, router, HIN overlay, POIs, and both views are unchanged.

**Tech stack delta:** add `osmnx` (graph build) + keep `geopandas`/`shapely`/`pyproj`. Remove Docker/brokenspoke. `pytest` + `responses` for tests as today.

**Working state at end of plan:** `make refresh` (no Docker) produces a valid `data/bikemap.db` with `streets.lts` / `intersections.lts_approach` populated from Mellow + CDOT, plus `lts-network.geojson.gz`. `make test` passes clean.

---

## Phase 0 — Setup

- [ ] **0a. Worktree.** Create a feature worktree off main (per Session Start Workflow). Branch: `feat/mellow-cdot-scoring`.
- [ ] **0b. Dependency.** Add `osmnx` to `requirements.txt`; `pip install`. Verify `import osmnx` in the venv. (Sandbox must be up, or run in Claude Code.)
- [ ] **0c. Discover CDOT endpoints.** Query the `chicago.maps.arcgis.com` org for the `Chicago_Bike_Facilities_2023` and `Chicago_Off_Street_Bike_Trails` FeatureServer URLs + the facility-type field name. Record both in `prep/config/sources.yaml` and `docs/dataset-ids.md`. Verify with a 1-record `outSR=4326` query. **Verification:** facility-type field returns values from {Protected Bike Lane, Neighborhood Greenway, Buffered Bike Lane, Bike Lane, Marked Shared Lane, Signed Bike Route}.

## Phase 1 — Pure classifier (no I/O, fully unit-tested)

- [ ] **1a. RED:** `tests/prep/test_tier_classifier.py` — truth table for a new `prep/scoring/classifier.py::classify_tier(mellow_kind, cdot_facility)`. Cases: each Mellow kind alone; each CDOT facility alone; both-agree; both-disagree (CDOT wins); Mellow **path** + worse CDOT (stays tier 1 — the floor); neither (tier 3).
- [ ] **1b. GREEN:** implement `classify_tier` + the `CDOT_FACILITY_TO_TIER` and `MELLOW_KIND_TO_TIER` tables per design §1.1. Keep it a pure function over enums/strings — no geometry.
- [ ] **1c. REFACTOR:** move the facility/kind vocab into module constants; assert exhaustiveness (unknown facility string → baseline, logged).

## Phase 2 — Source parsers

- [ ] **2a. Mellow fetch + parse.** RED: `tests/prep/test_mellow_fetcher.py` with a small fixture mirroring a Django `mbm.mellowroute` dumpdata record (fields incl. `type`/kind + geometry/ways). GREEN: `prep/fetchers/mellow.py` downloads the fixture file(s) from `jeancochrane/mellow-bike-map` `app/mbm/fixtures/` and yields `MellowFeature(kind, geometry)`. Reproject to EPSG:4326. **Verification:** parses route/street/path into the three kinds.
- [ ] **2b. CDOT facilities fetch + parse.** RED: `tests/prep/test_cdot_facilities_fetcher.py` using a saved FeatureServer JSON fixture. GREEN: `prep/fetchers/cdot_facilities.py` (ArcGIS query, `outSR=4326`, paginated) yields `CdotFacility(facility_type, geometry)` for on-street + off-street trail layers. Mirror the existing `prep/fetchers/hin.py` ArcGIS pattern. **Verification:** facility-type strings map through `CDOT_FACILITY_TO_TIER`.

## Phase 3 — OSM graph builder

- [ ] **3a. RED:** `tests/prep/test_osm_graph.py` — feed a tiny saved OSM graph (osmnx graphml fixture or a hand-built MultiDiGraph) and assert `build_street_edges(graph)` yields edge records with: stable `head_node_id`/`tail_node_id`, LineString geom (EPSG:4326), `length_m`, `highway`, `name`. Assert the graph is connected.
- [ ] **3b. GREEN:** `prep/graph/osm_builder.py` — `osmnx.graph_from_bbox(target.bbox, network_type="bike")`, simplify, project to 4326, emit edge + node records matching the `streets`/`intersections` schema (synthesize a stable `road_id` per edge; keep `osm_id` = osmnx `osmid`). **Verification:** node IDs at edge ends are consistent (shared between adjacent edges) so the router can traverse.

## Phase 4 — Spatial classify (graph × sources)

- [ ] **4a. RED:** `tests/prep/test_classify_network.py` — given OSM edges + Mellow features + CDOT facilities (all fixtures), assert each edge gets the correct tier via buffer-match (reuse the `prep/joins/hin_to_osm.py` matching approach). Include the Mellow-path-floor case end-to-end.
- [ ] **4b. GREEN:** `prep/scoring/classify_network.py` — for each edge: nearest/overlapping Mellow feature → baseline; nearest/overlapping CDOT facility → override via `classify_tier`; apply path floor. Attach `lts` to each edge record.
- [ ] **4c. Intersections.** RED+GREEN: `prep/scoring/intersection_tiers.py::lts_approach_for_node` = max incident edge tier. Replaces `synthesize_intersections` for the new graph. **Verification:** node tier = worst incident edge.

## Phase 5 — Pipeline wiring

- [ ] **5a. sources.yaml.** Add `mellow`, `cdot_bike_network`, `cdot_off_street_trails`; remove the `brokenspoke:` block. Update `prep/config_loader.py` + `tests/prep/test_config_loader.py` accordingly.
- [ ] **5b. main.py.** RED: extend `tests/prep/test_main.py`. GREEN: in `prep/main.py` replace the brokenspoke run + `ingest_segments` (steps 2 & 5) with: fetch Mellow + CDOT → `build_street_edges` → `classify_network` → `intersection_tiers`. Keep HIN join (re-key onto new edge ids), POI ingest, treatments, atomic swap, `lts-network` export. Remove `--skip-brokenspoke`.
- [ ] **5c. Delete dead code.** Remove `prep/lts/runner.py`, `docker/compose.brokenspoke.yml`, brokenspoke refs; trim `prep/lts/ingest.py` to the POI helpers only (or move them to `prep/fetchers`). Update imports.
- [ ] **5d. Reporting.** Keep `prep/reporting/lts_diff.py` (still diffs `streets.lts`); retitle output to reflect Mellow/CDOT. Update `prep/reporting/prep_report.py` source list.

## Phase 6 — Verify & integrate

- [ ] **6a.** `make test` green (ruff + mypy + pytest). Fix any app/router/test regressions (schema is unchanged, so these should pass untouched).
- [ ] **6b.** Small-bbox integration build (e.g. a few sq-mi around a Mellow-mapped neighborhood): run `prep/main.py` against the mini bbox, assert DB builds, `streets.lts` distribution is plausible (not all tier 3), graph connected, `/explore` GeoJSON exports.
- [ ] **6c.** Full Chicago `make refresh`; eyeball `prep_report.md` + the `/explore` map: protected lanes/greenways render green, mellow streets amber, everything else red. Spot-check 3–4 known protected-lane streets (e.g. a known PBL corridor) read tier 1.
- [ ] **6d.** README: replace Docker/brokenspoke setup with the osmnx flow.
- [ ] **6e.** Review against this plan + design; deploy note for Railway (no Docker-in-prep means simpler build).

## Notes / risks

- **Mellow coverage is partial** (volunteer-mapped, North Side strongest). Streets outside Mellow + CDOT fall to tier 3 by default — expected, but verify it doesn't make routing infeasible in under-mapped areas. The tier *fallback* weights in `routing_weights.yaml` already soften hard cutoffs.
- **Graph keying** is the riskiest change: the HIN join and `lts_diff` both key off the old PFB `road_id`. Phase 5b must re-key cleanly; lean on `test_hin_to_osm.py` + `test_lts_diff.py`.
- **Licensing:** Mellow is MIT; CDOT data is public. Add attribution to the README/footer.
