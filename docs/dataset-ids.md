# Verified Chicago Data Portal Dataset IDs

Verified 2026-05-05 against `https://data.cityofchicago.org/api/views/<id>.json`.

## Verified — used in `prep/config/sources.yaml`

| ID | Name | Use | Status |
|---|---|---|---|
| `hvv9-38ut` | Bike Routes | CDOT bike facilities (sanity check vs OSM) | ✅ 200 OK, 11 cols including `the_geom` |
| `htai-wnw4` | Ward Offices | Alderman office POIs | ✅ 200 OK, 8 cols including geocoded address |
| `x8fc-8rcq` | Libraries - Locations, Contact Information, and Usual Hours of Operation | CPL branch POIs | ✅ 200 OK, 8 cols including geocoded address |

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
