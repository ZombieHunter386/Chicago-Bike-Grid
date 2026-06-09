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
`a693899119c34a71a1e9120802633e6a` → operational layers. Both are hosted on
`services7.arcgis.com/A03QrhyHnDaUmK0W` and honor `outSR=4326` (response
`spatialReference.latestWkid == 4326`).

| Layer | FeatureServer URL | Geom | Count | Status |
|---|---|---|---|---|
| `Chicago_Bike_Facilities_2023` (on-street) | `.../Chicago_Bike_Facilities_2023/FeatureServer/0` | Polyline | 953 | ✅ 1-rec `outSR=4326` OK |
| `Chicago_Off_Street_Bike_Trails` (off-street) | `.../Chicago_Off_Street_Bike_Trails/FeatureServer/0` | Polyline | 89 | ✅ 1-rec `outSR=4326` OK |

**Facility-type field:** `DISPLAYROU` (alias "Bike Facility Type").

On-street distinct `DISPLAYROU` values (953 features):

- `PROTECTED BIKE LANE` → tier 1
- `NEIGHBORHOOD GREENWAY` → tier 1
- `BUFFERED BIKE LANE` → tier 2
- `BIKE LANE` → tier 2
- `SHARED-LANE` → tier 2 *(see delta below)*

Off-street `DISPLAYROU` values: `OFF-STREET TRAIL`, `ACCESS PATH`, blank — but the
whole `Chicago_Off_Street_Bike_Trails` layer maps to **tier 1** unconditionally
per design §1.1, so its field values are not used for tiering.

### Deltas from design §1.1 (flag before Phase 1)

1. **Field is `DISPLAYROU`**, not a generically-named "facility type" field.
2. **Vocabulary is UPPERCASE** and hyphenated (`SHARED-LANE`), not the title-case
   strings in §1.1. The `CDOT_FACILITY_TO_TIER` table must key on the actual values.
3. **No `Signed Bike Route` value exists.** §1.1 listed both "Marked Shared Lane"
   and "Signed Bike Route"; in the live data these collapse into the single
   `SHARED-LANE`. Default mapping is tier 2 (§7 refinement allows flipping to 3).
4. The web map also exposes many **newer** snapshots (Bike Network Sept/Dec 2025,
   Trails Sept 2025). Design locked the `Chicago_Bike_Facilities_2023` +
   `Chicago_Off_Street_Bike_Trails` names; using those. Newer layers available if
   fresher data is wanted later.

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
