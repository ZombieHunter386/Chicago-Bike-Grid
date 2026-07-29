# Verified Chicago Data Portal Dataset IDs

Verified 2026-05-05 against `https://data.cityofchicago.org/api/views/<id>.json`.

## Verified — used in `prep/config/sources.yaml`

| ID | Name | Use | Status |
|---|---|---|---|
| `hvv9-38ut` | Bike Routes | CDOT bike facilities (sanity check vs OSM) | ✅ 200 OK, 11 cols including `the_geom` |
| `htai-wnw4` | Ward Offices | Alderman office POIs | ✅ 200 OK, 8 cols including geocoded address |
| `x8fc-8rcq` | Libraries - Locations, Contact Information, and Usual Hours of Operation | CPL branch POIs | ✅ 200 OK, 8 cols including geocoded address |

## CDOT bike-facility ArcGIS layers (Mellow + CDOT scoring, verified 2026-06-09)

Discovered from the CDOT **"Existing Chicago Bike Facilities"** instant app
(appid `d4085fb1e59b4eb69a119a4428868ee6`, owner `CDOT_PUB`) → web map
`a693899119c34a71a1e9120802633e6a` → operational layers. All honor `outSR=4326`
(response `spatialReference.latestWkid == 4326`).

**Chosen layers (user decision 2026-06-09 — prefer current data that exports geometry):**

| Role | Layer | Host | Geom | Count | Edited | Status |
|---|---|---|---|---|---|---|
| on-street | `Bikeway_Network_2024_Final_Public` | `services.arcgis.com/G3nmNsarwQblLhip` | Polyline | 931 | Jan 2025 | ✅ geometry @ `outSR=4326` |
| off-street | `Trails_Network_2024_11_18` | `services7.arcgis.com/A03QrhyHnDaUmK0W` | Polyline | 88 | Nov 2024 | ✅ geometry @ `outSR=4326` |

**On-street facility-type field:** `BIKE_DSPLY` (abbreviated single-word values).

On-street `BIKE_DSPLY` value → tier (931 features):

- `PROTECTED` (154) → tier 1
- `NEIGHBORHOOD` (203) → tier 1
- `BUFFERED` (188) → tier 2
- `BIKE` (276) → tier 2
- `SHARED` (110) → **tier 3** *(sharrow / no physical lane — user decision 2026-06-09)*

Off-street: the whole `Trails_Network_2024_11_18` layer maps to **tier 1**
unconditionally per design §1.1, so its field values are not used for tiering.

### Layer-selection notes & deltas from design §1.1

1. **Design §1.1 vocabulary differs from the live data.** This Jan-2025 layer's
   field is `BIKE_DSPLY` with abbreviated values (`PROTECTED`, `NEIGHBORHOOD`,
   `BUFFERED`, `BIKE`, `SHARED`), not the title-case strings in §1.1. The
   `CDOT_FACILITY_TO_TIER` table keys on these actual values.
2. **`SHARED` → tier 3** (not tier 2). §1.1 had sharrows/marked-shared at tier 2;
   user chose tier 3 (no physical lane) on 2026-06-09.
3. **Newer 2025 snapshots are geometry-locked.** `BikeNetwork_2025_09_26_web`
   (Sep 2025) and `Dec2025_Bike_Network_Internal_Basemap` (Dec 2025) only support
   attribute queries — `returnGeometry=true` returns HTTP 400. Unusable for the
   OSM spatial match. The Jan-2025 `…Final_Public` layer is the most current one
   that exports geometry.
4. **Stable fallback:** `Chicago_Bike_Facilities_2023`
   (`services7.../A03QrhyHnDaUmK0W`, field `DISPLAYROU`, full-name vocab
   `PROTECTED BIKE LANE` / `NEIGHBORHOOD GREENWAY` / `BUFFERED BIKE LANE` /
   `BIKE LANE` / `SHARED-LANE`, 953 features, Apr 2023). Swap via `sources.yaml`
   `on_street_url` + `facility_type_field` if the Jan-2025 layer regresses.

## Not used / not available

| ID | Notes |
|---|---|
| `3w5d-sru8` | "Bike Routes - Map" — view-only (0 cols), not tabular. Replaced by `hvv9-38ut`. |
| `spqx-js37` | "Red Light Camera Violations" — wrong dataset. Was a placeholder mistake in the original plan draft. |

## Speed limits — no usable Chicago Data Portal source

Searched CDP catalog for speed-limit tabular datasets. Findings:

- `rbfp-3tic` "Speed Limits Map" — 0 cols, view-only.
- `7n5j-865y` "VZV Speed Limits" — 0 cols, view-only.
- No tabular speed-limit dataset is currently published on `data.cityofchicago.org`.

**Resolution:** drop `chicago_speed_limits` entry from `sources.yaml`. Speed data comes from OSM (via `osmnx` and `brokenspoke-analyzer`). Per spec §3.3, OSM is source of truth and our CDP speed_limits fetcher was meant only to *supplement* OSM where missing — without a CDP source, brokenspoke uses OSM `maxspeed` tags directly.

The orchestrator (Task 22) already handles `if speed_src is not None:` gracefully. Task 9 (speed limits fetcher) is now a no-op stub that exists only to avoid breaking imports if some future contributor re-adds the source.

If a usable speed-limit dataset surfaces later (e.g., CDOT publishes one), add a new entry to `sources.yaml` and revive Task 9 with the verified ID.

## Cook County Level of Traffic Stress (2023) — verified 2026-07-29

The stress baseline for the whole app as of 2026-07-29 (design
`docs/specs/2026-07-29-cook-county-lts4-design.md`), replacing the Mellow Bike
Map. Discovered from the Cook County open-data hub slug
`cookcountyil::level-of-traffic-stress-2023`.

| Field | Value |
|---|---|
| Layer | `https://gis.cookcountyil.gov/traditional/rest/services/DOTH_expanded/MapServer/14` |
| Service | Cook County DoTH `DOTH_expanded` (MapServer), layer 14 of a 30+ layer highway-assets service |
| Hub page | `https://hub-cookcountyil.opendata.arcgis.com/datasets/cookcountyil::level-of-traffic-stress-2023` |
| AGOL item | `12834166eae542a59641212c882eff0c_14` |
| Records | 207,459 polylines |
| Distribution | LTS 1 = 153,880 · LTS 2 = 2,858 · LTS 3 = 10,985 · LTS 4 = 39,736 |
| Fields used | `way_id` (`esriFieldTypeDouble` — a real OSM way id) · `lts` (`esriFieldTypeString`, `"1"`–`"4"`) |
| Native CRS | WKID 102671 (NAD83 / Illinois East, US ft) — irrelevant to us, see below |
| Pagination | `maxRecordCount` 2000, `supportsPagination: true` |
| Refresh | annual (county-stated); the layer is a **2023** OSM snapshot |

Methodology: LTS computed by the University of Minnesota Accessibility
Observatory method over 2023 OpenStreetMap — roadway tag, restrictions,
shared-street and shared-busway status, lane configuration, posted speed, and
existing bike facilities. County states it is "for planning-level purposes only."

**Why the fetch is attribute-only.** Because the county derived LTS *from OSM*,
`way_id` is a genuine OSM way id — spot-verified 2026-07-29: `24072568` =
North Marmora Avenue, `24073103` = West Dakin Street. So the join to our osmnx
edges is an exact dict lookup on `OsmEdge.osm_way_ids` and we never request
geometry (`returnGeometry=false`), which sidesteps the 102671 reprojection
entirely and keeps the fetch to ~104 attribute pages.

Caveats:
- Coverage is the Chicago **metropolitan area**, wider than our `target.bbox`;
  extra ways simply never match an edge.
- `name` is present but frequently a single space; unused.
- Ways created or renumbered in OSM since 2023 won't match — the classifier
  falls back to the OSM `highway` class and `prep_report.md` publishes the
  match rate so drift is visible each run.

### CDOT bike facilities — retained as an improve-only override

The CDOT layers recorded earlier in this file (`Bikeway_Network_2024_Final_Public`
field `BIKE_DSPLY`, plus `Trails_Network_2024_11_18`) were **not** retired by the
Cook County migration. They now supply an improve-only override on top of the
county baseline, because CDOT is current to Jan 2025 while the county layer is a
2023 snapshot. Live check 2026-07-29 — all 931 on-street features resolve
against the classifier's vocabulary, no unrecognized values:

| `BIKE_DSPLY` | n | Effect |
|---|---|---|
| `BIKE` | 276 | override → LTS 2 |
| `NEIGHBORHOOD` | 203 | override → LTS 1 |
| `BUFFERED` | 188 | override → LTS 2 |
| `PROTECTED` | 154 | override → LTS 1 |
| `SHARED` | 110 | **no override** (sharrow) |
