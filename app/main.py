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

import os
import sqlite3
from pathlib import Path

from flask import Flask, jsonify
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


def create_app(
    *,
    bikemap_db: Path,
    cache_db: Path,
    nominatim_user_agent: str,
    min_streets: int = 10000,
) -> Flask:
    _validate_bikemap(bikemap_db, min_streets)

    init_cache_db(cache_db, fingerprint=bikemap_fingerprint(bikemap_db))
    snap = load_graph(bikemap_db)
    pois_by_category = load_pois(bikemap_db)

    app = Flask(__name__, static_folder="static", static_url_path="/static")

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
