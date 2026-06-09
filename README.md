# Chicago Bike Grid

A public web tool that turns a Chicago resident's home address and personal destinations into a printable, shareable advocacy artifact showing where bike infrastructure investment would most change their life.

Two views ship in v1:
- **Advocacy view** (`/`): enter your home address, pick destinations, see fast vs. safe routes and the avoided-intersections worth fixing.
- **LTS Data Explorer** (`/explore`): the underlying Level-of-Traffic-Stress network for the whole city, colored by stress, with an optional High-Injury Network overlay.

## Setup

1. Install Python 3.11+. (No Docker needed — the prep pipeline builds the routing graph from OpenStreetMap via `osmnx` and classifies bike-stress tiers from the Mellow Bike Map + CDOT bike-facility layers.)
2. `python -m venv .venv && source .venv/bin/activate`
3. `pip install -r requirements.txt`
4. Optionally `cp .env.example .env` and fill in `SOCRATA_APP_TOKEN`.

## Usage

```bash
# Run the full prep pipeline (~30-90 min for Chicago) — produces data/bikemap.db
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

Design specs at `docs/specs/`; implementation plans at `docs/plans/`. The master design doc is [`docs/specs/2026-05-04-chicago-bike-advocacy-map-design.md`](docs/specs/2026-05-04-chicago-bike-advocacy-map-design.md); the LTS Explorer addition is [`docs/specs/2026-05-11-lts-data-explorer-design.md`](docs/specs/2026-05-11-lts-data-explorer-design.md).
