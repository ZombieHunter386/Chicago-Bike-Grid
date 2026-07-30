# prep/osm_config.py
"""Shared osmnx settings for every Overpass consumer in the prep pipeline.

Both the POI fetcher and the street-graph builder talk to Overpass through
osmnx, and both need the same two things: a cache directory inside
``data/cache/`` (gitignored), and a configurable endpoint.

The endpoint matters at county scale. osmnx tiles any bbox above its query
limit, so widening the target from the City of Chicago to all of Cook County
(3.2x the area, 2026-07-30) multiplied the number of Overpass requests — enough
that the public ``overpass-api.de`` instance started refusing connections
mid-run and failed the build. The graph tiles are cached, so a retry is much
lighter, but a single hard-coded endpoint leaves no recourse while a ban is in
force. ``OVERPASS_URL`` lets a rebuild point elsewhere without a code change.

**Any replacement MUST serve planet-wide data.** Most public Overpass mirrors
are regional extracts, and a regional instance does not error on an
out-of-region query — it returns a perfectly well-formed *empty* result, which
would sail through the pipeline and produce a silently empty map. Verified
2026-07-30: ``overpass.osm.ch`` answers a Zurich pharmacy query with 27 and the
identical Chicago query with 0, so it is a Switzerland extract and unusable
here; ``overpass.kumi.systems`` returned an empty body for the same probe.
Before pointing this at a new host, run the same two-city probe and confirm the
Chicago count is non-zero.

Note the value is the API *base*, not the ``/interpreter`` path — that is what
``osmnx.settings.overpass_url`` expects.
"""

from __future__ import annotations

import os

DEFAULT_OVERPASS_URL = "https://overpass-api.de/api"
CACHE_FOLDER = "data/cache/osmnx"


def configure_osmnx(ox: object, requests_timeout: int | None = None) -> str:
    """Apply shared cache/endpoint settings to an imported ``osmnx`` module.

    Takes the module rather than importing it so callers keep their lazy
    imports (osmnx is heavyweight and slow to import at test collection).
    Returns the Overpass URL actually in use, for logging.
    """
    settings = ox.settings  # type: ignore[attr-defined]
    settings.cache_folder = CACHE_FOLDER
    # Leave osmnx's own rate limiter on: it reads the server's slot
    # availability and paces requests, which is what keeps a large tiled
    # download from tripping the public instance's abuse guard.
    settings.overpass_rate_limit = True
    url = os.environ.get("OVERPASS_URL", "").strip() or DEFAULT_OVERPASS_URL
    settings.overpass_url = url
    if requests_timeout is not None:
        settings.requests_timeout = requests_timeout
    return url
