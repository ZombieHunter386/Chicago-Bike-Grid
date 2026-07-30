# Chicago Bike Grid

A public web tool that turns a Cook County resident's home address and personal destinations into a printable, shareable advocacy artifact showing where bike infrastructure investment would most change their life.

Coverage is all of Cook County, Illinois (expanded from the City of Chicago on 2026-07-30). The service area is set by `target.bbox` in `prep/config/sources.yaml`; the geocoder's Nominatim viewbox is pinned to the same bounds by a test, since addresses outside it cannot be routed.

Two views ship in v1:
- **Advocacy view** (`/`): enter your home address, pick destinations, see fast vs. safe routes and the avoided-intersections worth fixing. Routes are planned for one of four riders — *Safe for kid* (LTS 1), *Inexperienced* (LTS 1–2), *Experienced* (LTS 1–3), or *Death wish* (LTS 1–4).
- **LTS Data Explorer** (`/explore`): the underlying Level-of-Traffic-Stress network for the whole county, colored by stress on the four-level scale, with an optional High-Injury Network overlay.

## Street stress (LTS)

Every street carries a Level of Traffic Stress from 1 (calm) to 4 (hostile), the standard 4-level scale, built from two sources:

- **Cook County DoTH's published LTS (2023)** is the baseline, joined to our OSM graph by way ID. The county computes it with the University of Minnesota Accessibility Observatory methodology over 2023 OSM — road class, speed, lane configuration, and existing bike facilities. Ways absent from the snapshot fall back to their OSM `highway` class.
- **CDOT's bike-facility layers** (current to Jan 2025) apply an *improve-only* override: a protected lane, greenway, or off-street trail can pull a street down to LTS 1, and a buffered or standard lane to LTS 2, but CDOT never raises an LTS. Sharrows apply no override. This keeps facilities built after the county's 2023 snapshot visible without letting a paint stripe override the county's traffic modelling. See [the design doc](docs/specs/2026-07-29-cook-county-lts4-design.md) §3.3.

`prep_report.md` reports the county way-ID match rate and how many edges CDOT improved, so each source's contribution is visible after every run.

## Setup

1. Install Python 3.11+. (No Docker needed — the prep pipeline builds the routing graph from OpenStreetMap via `osmnx`, then attaches Cook County's published LTS 1–4 by OSM way ID with CDOT bike facilities as an improve-only override.)
2. `python -m venv .venv && source .venv/bin/activate`
3. `pip install -r requirements.txt`
4. Optionally `cp .env.example .env` and fill in `SOCRATA_APP_TOKEN`.

## Usage

```bash
# Run the full prep pipeline (a few hours for Cook County) — produces data/bikemap.db
# and data/lts-network.geojson.gz
make refresh

# View the run report
make report

# Local web server (loads bikemap.db, serves / and /explore)
make dev

# Run tests (ruff + mypy + fast pytest)
make test
```

## Outputs

- `data/bikemap.db` — primary SQLite + SpatiaLite database consumed by the web service.
- `data/lts-network.geojson.gz` — pre-built gzipped GeoJSON served by `/lts-network` for the Explorer view.
- `prep_report.md` — per-source OK/WARN/FAIL + record-count deltas + LTS regression diff.
- `hin_match_report.md` — list of HIN features that didn't match any OSM feature.
- `lts_diff.md` — per-segment LTS changes vs. previous prep run.

## Docs

Design specs at `docs/specs/`; implementation plans at `docs/plans/`. The master design doc is [`docs/specs/2026-05-04-chicago-bike-advocacy-map-design.md`](docs/specs/2026-05-04-chicago-bike-advocacy-map-design.md); the LTS Explorer addition is [`docs/specs/2026-05-11-lts-data-explorer-design.md`](docs/specs/2026-05-11-lts-data-explorer-design.md). The current scoring model (Cook County LTS 1–4 + CDOT override, four route personas) is [`docs/specs/2026-07-29-cook-county-lts4-design.md`](docs/specs/2026-07-29-cook-county-lts4-design.md), which supersedes the Mellow+CDOT model in [`docs/specs/2026-06-09-mellow-cdot-scoring-design.md`](docs/specs/2026-06-09-mellow-cdot-scoring-design.md).
