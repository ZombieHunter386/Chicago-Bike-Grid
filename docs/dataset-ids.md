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
