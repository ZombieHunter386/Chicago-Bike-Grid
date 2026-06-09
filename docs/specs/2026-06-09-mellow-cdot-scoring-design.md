# Mellow + CDOT scoring — Design

**Date**: 2026-06-09
**Status**: DRAFT — awaiting user sign-off on architecture before implementation planning
**Scope**: Replaces the segment-stress *source* for the Chicago Bike Grid. The brokenspoke / PFB LTS computation is removed entirely. Street stress is derived instead from the Mellow Bike Map (baseline) plus the City of Chicago / CDOT bike-lane network (override). The 3-tier routing model, the tier→weight tables, the HIN overlay, the POI layers, and the `/` and `/explore` views are unchanged in shape.

---

## 1. Purpose & decisions

Today every street segment's stress (`streets.lts ∈ {1,2,3}`) is computed by the PeopleForBikes brokenspoke-analyzer from OSM. We are replacing that with a classification built from two human/agency-curated sources:

- **Mellow Bike Map** (`jeancochrane/mellow-bike-map`, MIT) — a hand-curated set of calm/mellow routes for Chicago, already organized into three route kinds: official **routes**, mellow **streets**, and protected off-street **paths**.
- **City of Chicago / CDOT bike network** — every on-street bike facility, attributed by facility type (protected bike lane, buffered, neighborhood greenway, standard/shared lane, off-street trail).

Decisions locked with the user (2026-06-09):

1. **Combine logic: CDOT lane type wins.** Mellow is the baseline for every street; where CDOT shows a bike facility on that street, the CDOT facility type sets the tier.
2. **Fully replace brokenspoke.** Drop the Docker/PFB pipeline; Mellow + CDOT + OSM become the only stress inputs.
3. **Agent sources the data** (Mellow fixtures from GitHub; CDOT layer TBD — see §7).

### 1.1 Tier mapping (the new scoring rule)

The user's rule, expressed against the existing tier scale (1 = safest).

**Step 1 — Mellow baseline** (every street):

- Mellow **path** (protected off-street) → tier 1
- Mellow **street** (mellow calm street) → tier 2
- Mellow **route** (official on-street route) → tier 2
- Not in Mellow at all → tier 3

**Step 2 — CDOT override** (CDOT facility type wins where it covers the street).

The live CDOT layer (chosen 2026-06-09: `Bikeway_Network_2024_Final_Public`, Jan
2025, field `BIKE_DSPLY`) uses **abbreviated single-word values**, not the
title-case strings originally drafted here. The classifier keys on these actual
values (see `docs/dataset-ids.md` for the discovery record):

| `BIKE_DSPLY` value | Tier | Rationale |
|---|---|---|
| `PROTECTED` | **1** | protected → kid |
| `NEIGHBORHOOD` | **1** | greenway → kid |
| Off-Street Trail layer (`Trails_Network_2024_11_18`, whole layer) | **1** | protected/off-street → kid |
| `BUFFERED` | **2** | a lane, not protected → parent |
| `BIKE` | **2** | a lane, not protected → parent |
| `SHARED` | **3** | sharrow — no physical lane (user decision 2026-06-09; supersedes the §7 tier-2 default) |

Unknown `BIKE_DSPLY` values fall through to the Mellow baseline (logged). The
stable fallback layer `Chicago_Bike_Facilities_2023` uses field `DISPLAYROU` with
full-name values (`PROTECTED BIKE LANE`, `NEIGHBORHOOD GREENWAY`, `BUFFERED BIKE
LANE`, `BIKE LANE`, `SHARED-LANE`); if it is ever swapped back in, the
`CDOT_FACILITY_TO_TIER` table must cover both vocabularies.

**Step 3 — Mellow-path floor (locked 2026-06-09):** CDOT must **not** downgrade a Mellow tier-1 **path** below tier 1. Formally, for a Mellow path segment the final tier is `min(1, cdot_tier)` = 1. For all other segments, `final = cdot_tier if CDOT covers it else mellow_baseline`. In practice off-street paths rarely appear in the on-street CDOT layer, so this only matters at edge cases.

Net effect: a street is tier 1 only if it's a Mellow path **or** CDOT marks it protected / greenway / off-street; tier 2 if it has any lesser facility or is a mellow/route street; tier 3 otherwise.

## 2. The architectural wrinkle: brokenspoke is also the graph

This is the part that makes "just swap the score" bigger than it sounds.

`prep/db/schema.sql` `streets` table is populated **entirely** from PFB's `neighborhood_ways.shp` — not only `lts`, but the routing graph itself: `road_id` (PK), `osm_id`, `head_node_osm_id` / `tail_node_osm_id` (the topology the router walks), `geom`, `length_m`, `highway`, `speed`. Intersections (`intersections.lts_approach`) likewise come from PFB's per-approach LTS.

So removing brokenspoke removes **the source of the graph topology**, not just the stress number. We need a replacement that yields a connected, routable street graph for Chicago plus per-edge geometry and node IDs.

### 2.1 Chosen approach — build the graph from OSM directly

Build the base network from OpenStreetMap (the same substrate brokenspoke and Mellow both use), then attach a tier to each edge from Mellow + CDOT.

- **Graph build**: extract the Chicago drivable/bikeable street network from an OSM extract within `target.bbox`, producing edges (LineString geometry, stable node IDs at each end) and nodes. Candidate tooling: `osmnx` (Python, simplest to integrate into the existing Python prep package and to unit-test) or `osm2pgrouting` (what Mellow uses; heavier, needs Postgres). **Recommendation: `osmnx`** — keeps the prep pipeline pure-Python, no new DB service, and emits exactly the node/edge model the `streets`/`intersections` schema already expects.
- **Tier attach** (revised 2026-06-09 against the live Mellow fixture):
  - **Mellow** is matched by **OSM way-ID join, not geometry**. The `mbm.mellowroute` records carry no per-route LineString — each has a `type` (kind) and a `ways` list of OSM way IDs (plus only a coarse `bounding_box`). Build `way_id -> kind` (best/min tier on conflict) and match each OSM edge by its `osmid` (osmnx edges carry the OSM way id; simplified edges carry a *list*, so an edge is kind X if **any** of its osmids is in the kind-X set). Exact, and sidesteps buffer fuzz.
  - **CDOT** is still matched **spatially** per edge, mirroring the existing `prep/joins/hin_to_osm.py` buffer-match pattern.
  - Apply the §1.1 rule (Mellow baseline → CDOT override → path floor).
- **Intersections**: `lts_approach` no longer exists as a PFB output. v1 rule: an intersection node's `lts_approach` = max (worst) tier of its incident edges. Keeps the schema NOT NULL constraint satisfied and the `/explore` intersection layer meaningful.

### 2.2 What this preserves

- Schema shape: `streets.lts` and `intersections.lts_approach` stay `INTEGER 1..3`. Tier→weight tables (`app/core/weights.py`, `prep/config/routing_weights.yaml`) unchanged. The router, HIN join, POI ingest, treatments, `/` and `/explore` views all keep working against the same columns.
- The HIN overlay still matches against the new OSM edges (the HIN join already matches against OSM geometry, not PFB specifics — minor key adjustment only).

### 2.3 What changes / what breaks (must address)

- `prep/lts/runner.py` (brokenspoke Docker driver), `docker/compose.brokenspoke.yml`, and the `brokenspoke:` block in `sources.yaml` become dead → remove.
- `prep/lts/ingest.py::ingest_segments` (reads `neighborhood_ways.shp`) → replaced by an OSM graph builder + Mellow/CDOT classifier. The POI ingest helpers in the same file are independent and stay.
- `prep/lts/synthesize_intersections.py` → replaced by the "max incident edge" rule.
- `prep/main.py` orchestration (steps 2 & 5) → swap brokenspoke run + `ingest_segments` for the new builder. POI/HIN/CDP fetchers stay.
- `prep/reporting/lts_diff.py` still works (diffs `streets.lts` between DB builds) but now reports Mellow/CDOT-driven changes rather than PFB changes — keep, retitle.
- `osm_id` keying: PFB used `road_id` per-block as the matching key. With osmnx, the natural PK is `(u, v, key)` or a synthesized edge id; the HIN-join `osm_id` plumbing in `main.py` needs a small reshape. Schema columns can stay (rename in comments only).
- README "Setup" step about Docker/brokenspoke → update.

## 3. New / changed data sources (`sources.yaml`)

```yaml
mellow:
  name: "Mellow Bike Map routes (jeancochrane/mellow-bike-map, MIT)"
  type: "github_fixture"
  # Django dumpdata fixtures of model mbm.mellowroute; route kind ∈ {route, street, path}
  fixtures_repo: "jeancochrane/mellow-bike-map"
  fixtures_path: "app/mbm/fixtures/"      # confirm exact files at build time
  refresh_cadence: "quarterly"

cdot_bike_network:
  name: "CDOT Chicago Bike Facilities (on-street, facility type)"
  type: "arcgis_feature_service"
  # CDOT 'Chicago Bike Facilities' instant app (appid d4085fb1e59b4eb69a119a4428868ee6).
  # On-street layer: Chicago_Bike_Facilities_2023. Facility-type field carries
  # {Protected Bike Lane, Neighborhood Greenway, Buffered Bike Lane, Bike Lane,
  #  Marked Shared Lane, Signed Bike Route}. Exact FeatureServer URL + field name
  # to be confirmed at build time by querying the chicago.maps.arcgis.com org.
  on_street_url: "TBD — Chicago_Bike_Facilities_2023 FeatureServer layer"
  refresh_cadence: "quarterly"

cdot_off_street_trails:
  name: "CDOT Chicago Off-Street Bike Trails"
  type: "arcgis_feature_service"
  # Off-street layer: Chicago_Off_Street_Bike_Trails (same app). All → tier 1.
  trails_url: "TBD — Chicago_Off_Street_Bike_Trails FeatureServer layer"
  refresh_cadence: "quarterly"
```

`hin`, `cdp_alderman_offices`, `cdp_library_branches`, `osm_pois` unchanged. The existing `cdot_bike_facilities` Socrata "sanity check only" source (`hvv9-38ut`) is superseded by the richer ArcGIS facility-type layers above; keep it as a cross-check or remove.

## 4. Build pipeline (new shape)

1. Fetch: HIN, CDOT bike network, CDP POIs, OSM POIs (unchanged fetchers) + **new** Mellow fixtures fetch + OSM street-network extract.
2. **Build graph** from OSM extract → edges + nodes (osmnx).
3. **Classify** each edge: Mellow baseline → CDOT override → tier 1/2/3 (§1.1).
4. Derive `intersections.lts_approach` = max incident edge tier.
5. HIN spatial join against new edges (existing buffer-match logic).
6. Write `streets`, `intersections`, `hin_features`, `pois`, `treatments`; atomic DB swap (unchanged).
7. Export `lts-network.geojson.gz` for `/explore` (unchanged exporter, new inputs).

## 5. Testing strategy (TDD, per Session Start Workflow)

- Unit: tier classifier truth table (every §1.1 combination, incl. Mellow-only, CDOT-only, both-agree, both-disagree → CDOT wins).
- Unit: Mellow fixture parser (route/street/path → geometry + kind).
- Unit: CDOT facility-type → tier mapping (each facility-type string).
- Unit: intersection `lts_approach` = max incident edge.
- Unit: OSM→Mellow **way-ID join** (dict lookup over edge osmids) and OSM→CDOT **spatial** match (fixture-based, mirrors `test_hin_to_osm.py`).
- Integration: small bbox end-to-end build → assert `streets.lts` distribution is sane and graph is connected.
- Regression: existing app/router tests stay green (schema unchanged).

## 6. Out of scope (v1)

- Per-direction tiers (keep single tier per edge).
- Time-of-day or seasonal stress.
- Blending Mellow + CDOT by *confidence* (we use a hard precedence, not a score).

## 7. Resolved decisions & remaining refinements

**Resolved with user (2026-06-09):**

1. **CDOT source** — discovered from the CDOT "Existing Chicago Bike Facilities" ArcGIS instant app (appid `d4085fb1e59b4eb69a119a4428868ee6`). Chosen 2026-06-09: on-street `Bikeway_Network_2024_Final_Public` (Jan 2025, field `BIKE_DSPLY`) + off-street `Trails_Network_2024_11_18`. The named-but-stale `Chicago_Bike_Facilities_2023` (field `DISPLAYROU`) is the documented fallback. The Sep/Dec-2025 snapshots were rejected — they are attribute-query-only (no geometry export). Full record in `docs/dataset-ids.md`.
2. **Mellow-path floor** — CDOT does **not** downgrade a Mellow tier-1 path. Locked into §1.1 Step 3.
3. **osmnx graph build** — confirmed (osmnx 2.1).
4. **Sharrows → tier 3** (user, 2026-06-09). `SHARED` has no physical lane, so it does not earn a parent-tier override; it falls to tier 3 unless Mellow rates the street. Encoded in the §1.1 Step-2 table.

**Remaining refinements (sane defaults chosen; user may adjust during implementation):**

- **CDOT on-street vs off-street trail layers** — both feed the classifier; off-street trails are unconditionally tier 1.

## 8. Blocker: execution environment

Implementation + testing need a real toolchain (osmnx, geopandas, pytest, git worktrees, running the prep build). The Cowork Linux sandbox is currently **down** ("Workspace unavailable / Download failed"), so the end-to-end build can't run here. Two options:

- **Recommended: execute in Claude Code.** This is local-repo, test-driven dev work that matches the Session Start Workflow (worktree → TDD → review → merge) and has a working toolchain. This design doc and the forthcoming `docs/plans/` plan live in the repo, so they carry over directly — open the repo in Claude Code and run the plan.
- **Stay in Cowork** once the sandbox is restored; the plan executes identically here.
