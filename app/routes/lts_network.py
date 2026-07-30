"""GET /lts-network — serves data/lts-network.geojson.gz, generated offline
by prep/lts_network_export (spec §5, Plan 2D)."""
from __future__ import annotations

from pathlib import Path

from flask import Blueprint, Response, send_from_directory
from flask_limiter import Limiter

_FILENAME = "lts-network.geojson.gz"


def build_lts_network_blueprint(data_dir: Path, limiter: Limiter) -> Blueprint:
    # send_from_directory resolves relative paths against Flask's
    # app.root_path (= the package dir), so we normalize to an absolute
    # filesystem path here so callers don't have to remember.
    data_dir = data_dir.resolve()
    bp = Blueprint("lts_network", __name__)

    @bp.get("/lts-network")
    @limiter.exempt
    def lts_network() -> Response:
        # send_from_directory handles ETag, If-None-Match, Last-Modified.
        # If the file doesn't exist it raises 404 — caller renders an
        # error card; see spec §6.4.
        resp = send_from_directory(
            data_dir, _FILENAME, max_age=0, conditional=True,
        )
        # `no-cache` means "cache it, but revalidate before every use" — NOT
        # "don't cache". This file is replaced wholesale on every prep refresh,
        # and it previously carried max-age=86400, so a returning visitor kept
        # the pre-refresh network for a full day with no revalidation: after the
        # 4-level LTS migration that meant 3-tier colors under a 4-level legend.
        # Revalidation is cheap — unchanged files 304 on the ETag with no body;
        # only a genuinely new export pays the ~2.5 MB transfer.
        resp.headers["Cache-Control"] = "no-cache"
        # Load-bearing: the .gz suffix masks .geojson from mimetypes, so
        # without this override Werkzeug returns application/octet-stream.
        # (Content-Encoding: gzip is auto-derived from the .gz extension.)
        resp.headers["Content-Type"] = "application/geo+json"
        return resp

    return bp
