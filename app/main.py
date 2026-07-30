"""Flask app factory.

Production entry: gunicorn -w 1 --threads 4 -b 0.0.0.0:$PORT app.main:app.
Local dev: `flask --app app.main run --no-reload` (avoid reloading the
30+s graph load on every code edit).

Env vars (read in __main__ block at the bottom):
  BIKEMAP_DB_PATH            default: data/bikemap.db
  CACHE_DB_PATH              default: data/cache.db
  NOMINATIM_USER_AGENT       default: chicago-bike-advocacy-map/1.0
  MIN_STREETS                default: 10000  (spec §3.10 startup validation)
"""
from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path

from flask import Flask, jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.middleware.proxy_fix import ProxyFix

from app.core.cache import bikemap_fingerprint, init_cache_db
from app.core.graph import load_graph
from app.core.poi_picker import load_pois
from app.routes.gap_analysis import build_gap_analysis_blueprint
from app.routes.geocode import build_geocode_blueprint
from app.routes.lts_network import build_lts_network_blueprint
from app.routes.pois import build_pois_blueprint
from app.routes.routing import build_routes_blueprint
from app.routes.treatments import build_treatments_blueprint

# Minimum bikemap.db schema version this code can read. Bump in lockstep
# with prep/db/builder.SCHEMA_VERSION and document the back-compat window
# (spec §3.11: code stays compatible with the last 2 schema versions).
MIN_SCHEMA_VERSION = 2


logger = logging.getLogger(__name__)


def _validate_bikemap(db_path: Path, min_streets: int) -> None:
    if not db_path.exists():
        raise FileNotFoundError(f"bikemap.db not found at {db_path}")
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        # Schema version check (Fix E). If the DB schema is older than
        # MIN_SCHEMA_VERSION, columns this code reads (e.g., streets.road_id)
        # may not exist — fail loudly at startup rather than at request time.
        sv_row = con.execute(
            "SELECT schema_version FROM schema_meta LIMIT 1"
        ).fetchone()
        if sv_row is None:
            raise RuntimeError("bikemap.db has no schema_meta row")
        schema_version = int(sv_row[0])
        if schema_version < MIN_SCHEMA_VERSION:
            raise RuntimeError(
                f"bikemap.db schema_version={schema_version} is older than "
                f"MIN_SCHEMA_VERSION={MIN_SCHEMA_VERSION}; rebuild the DB."
            )
        n = con.execute("SELECT COUNT(*) FROM streets").fetchone()[0]
    finally:
        con.close()
    if n < min_streets:
        raise RuntimeError(
            f"bikemap.db has {n} streets — below min_streets={min_streets}"
        )


def check_geocoder_covers_graph(snap: object) -> str | None:
    """Warn when the geocoder accepts addresses the routing graph can't serve.

    Three things must agree on the service area: `target.bbox` in
    sources.yaml (which streets get built), the Nominatim viewbox in
    routes/geocode.py (which addresses a user can enter), and the *data* —
    the bikemap.db actually deployed. A test pins the first two together, but
    nothing can pin the third: the DB is uploaded separately and a deploy can
    legitimately run new code against an older database for a few minutes.

    That gap is not theoretical. The 2026-07-30 Cook County expansion widened
    the bbox and viewbox together; had the code shipped before the rebuilt
    database, a suburban user would have geocoded successfully and only then
    hit "outside the graph's extent" at routing — strictly worse than today's
    clean "no results" at the search box.

    Returns a human-readable message when the viewbox reaches materially
    beyond the loaded graph, else None. Deliberately advisory: a stale-by-
    minutes DB during a rolling deploy must not take the site down.
    """
    import numpy as np

    from app.routes.geocode import _SERVICE_AREA_VIEWBOX

    coords = snap.vertex_coords_wgs84  # type: ignore[attr-defined]
    if coords is None or len(coords) == 0:
        return None
    left, top, right, bottom = (float(v) for v in _SERVICE_AREA_VIEWBOX.split(","))
    g_min_lat, g_max_lat = float(np.min(coords[:, 0])), float(np.max(coords[:, 0]))
    g_min_lng, g_max_lng = float(np.min(coords[:, 1])), float(np.max(coords[:, 1]))

    # Degrees of slack before we complain. The graph is clipped to the bbox and
    # its outermost intersection sits just inside, so a small shortfall is
    # normal; ~0.05 deg is roughly 5 km, far larger than that edge effect but
    # far smaller than a city-vs-county mismatch (which is ~0.35 deg here).
    tolerance = 0.05
    gaps = []
    if g_min_lat - bottom > tolerance:
        gaps.append(f"south (graph {g_min_lat:.4f} vs viewbox {bottom:.4f})")
    if top - g_max_lat > tolerance:
        gaps.append(f"north (graph {g_max_lat:.4f} vs viewbox {top:.4f})")
    if g_min_lng - left > tolerance:
        gaps.append(f"west (graph {g_min_lng:.4f} vs viewbox {left:.4f})")
    if right - g_max_lng > tolerance:
        gaps.append(f"east (graph {g_max_lng:.4f} vs viewbox {right:.4f})")
    if not gaps:
        return None
    return (
        "geocoder service area extends beyond the routing graph on: "
        + ", ".join(gaps)
        + " — addresses there will geocode but fail to route. "
        "Is bikemap.db older than the deployed config?"
    )


def create_app(
    *,
    bikemap_db: Path,
    cache_db: Path,
    nominatim_user_agent: str,
    min_streets: int = 10000,
) -> Flask:
    upload_token = os.environ.get("UPLOAD_TOKEN", "").strip()

    # First-boot bootstrap. When the persistent disk is empty and an
    # UPLOAD_TOKEN is configured, expose only /admin/upload-bikemap-db
    # + a 200-returning /health so Render routes traffic and `make
    # upload-db` can populate /var/data. On the next redeploy the file
    # exists and the normal-mode branch below boots the full app.
    if not bikemap_db.exists() and upload_token:
        return _make_admin_only_app(
            data_dir=bikemap_db.parent, upload_token=upload_token,
        )

    _validate_bikemap(bikemap_db, min_streets)

    init_cache_db(cache_db, fingerprint=bikemap_fingerprint(bikemap_db))
    snap = load_graph(bikemap_db)
    coverage_warning = check_geocoder_covers_graph(snap)
    if coverage_warning:
        logger.warning("%s", coverage_warning)
    pois_by_category = load_pois(bikemap_db)

    app = Flask(__name__, static_folder="static", static_url_path="/static")
    # Don't let the browser hold onto stale frontend assets across bugfixes,
    # but DO let it reuse unchanged files via 304 to keep page loads fast.
    # SEND_FILE_MAX_AGE_DEFAULT=0 sets max-age=0; combined with `no-cache`
    # (revalidate before reuse) and Flask's default ETag, the browser sends
    # If-None-Match → server returns 304 in ~10ms when the file hasn't
    # changed. When it HAS changed, the next request returns 200 with the
    # new bytes — bugfixes still propagate without a hard refresh.
    #
    # The earlier `no-store, must-revalidate` made every static file a full
    # fresh fetch on every page load (1–2s per file on this machine),
    # which read as "the app got much slower." `no-cache` alone is the
    # right primitive for this use case.
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

    @app.after_request
    def _no_cache_for_revalidation(resp):  # type: ignore[no-untyped-def]
        if request.path.startswith("/static/") or request.path in ("/", "/explore"):
            resp.headers["Cache-Control"] = "no-cache, max-age=0"
        return resp

    # Trust Render's reverse proxy: rewrite request.remote_addr from the first
    # X-Forwarded-For hop, and forward the original https/http scheme. Without
    # this, flask-limiter sees Render's edge IP for every request and rate-
    # limits all external users into one shared bucket (spec §3.10).
    # Render injects exactly one X-Forwarded-For hop, so x_for=1 is correct.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)  # type: ignore[method-assign]

    # Per-IP rate limiting (spec §3.10 — 60 req/min).
    limiter = Limiter(get_remote_address, app=app, default_limits=["60 per minute"])
    # Health endpoint is unlimited.
    app.register_blueprint(build_routes_blueprint(snap))
    app.register_blueprint(build_pois_blueprint(pois_by_category))
    app.register_blueprint(build_treatments_blueprint(bikemap_db))
    app.register_blueprint(build_geocode_blueprint(user_agent=nominatim_user_agent))
    app.register_blueprint(build_gap_analysis_blueprint(
        snap=snap, cache_db=cache_db, limiter=limiter,
    ))
    app.register_blueprint(build_lts_network_blueprint(
        data_dir=bikemap_db.parent,
        limiter=limiter,
    ))

    # Admin upload endpoint is wired ONLY when UPLOAD_TOKEN is set, so
    # untoken'd deploys don't expose the route at all (404, not 401).
    # See app/routes/admin.py for the atomicity contract.
    upload_token = os.environ.get("UPLOAD_TOKEN", "").strip()
    if upload_token:
        from app.routes.admin import build_admin_blueprint
        app.register_blueprint(build_admin_blueprint(
            data_dir=bikemap_db.parent,
            upload_token=upload_token,
        ))

    @app.get("/health")
    @limiter.exempt
    def health():  # type: ignore[no-untyped-def]
        return jsonify({"status": "ok", "streets": snap.g.ecount() // 2,
                        "vertices": snap.g.vcount()})

    from flask import send_from_directory

    @app.get("/")
    @limiter.exempt
    def index():  # type: ignore[no-untyped-def]
        return send_from_directory(app.static_folder, "index.html")

    @app.get("/explore")
    @limiter.exempt
    def explore():  # type: ignore[no-untyped-def]
        return send_from_directory(app.static_folder, "explore.html")

    return app


def _make_admin_only_app(*, data_dir: Path, upload_token: str) -> Flask:
    """Stub Flask app for the first-boot case where /var/data is empty.

    Exposes ONLY ``/admin/upload-bikemap-db`` (so the operator can populate
    the disk via ``make upload-db``) and a 200-returning ``/health`` (so
    Render routes traffic to the worker — otherwise the public URL never
    becomes reachable and we can't post the upload either). The status
    field flags the degraded state.

    After a successful upload + Render redeploy, ``create_app``'s normal
    branch takes over and the full app boots with the real bikemap.db.
    """
    from app.routes.admin import build_admin_blueprint

    app = Flask(__name__)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)  # type: ignore[method-assign]
    # 200 MB cap covers ~75 MB bikemap.db + ~7 MB geojson with headroom;
    # also stops an unauthenticated DoS from streaming infinite bytes.
    app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024

    app.register_blueprint(build_admin_blueprint(
        data_dir=data_dir, upload_token=upload_token,
    ))

    @app.get("/health")
    def health():  # type: ignore[no-untyped-def]
        return jsonify({
            "status": "awaiting_bootstrap",
            "reason": (
                "bikemap.db not present in data_dir; POST it via "
                "/admin/upload-bikemap-db then trigger a redeploy"
            ),
        })

    return app


def _make_default_app() -> Flask:
    return create_app(
        bikemap_db=Path(os.environ.get("BIKEMAP_DB_PATH", "data/bikemap.db")),
        cache_db=Path(os.environ.get("CACHE_DB_PATH", "data/cache.db")),
        nominatim_user_agent=os.environ.get(
            "NOMINATIM_USER_AGENT", "chicago-bike-advocacy-map/1.0",
        ),
        min_streets=int(os.environ.get("MIN_STREETS", "10000")),
    )


# WSGI entry point for gunicorn.
# Lazy: only build the app when imported by gunicorn, not at module-import time
# during testing.
if os.environ.get("APP_BOOTSTRAP", "0") == "1":
    app = _make_default_app()
