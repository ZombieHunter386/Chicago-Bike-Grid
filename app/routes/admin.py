"""POST /admin/upload-bikemap-db — token-gated dual-file atomic refresh.

Replaces ``data_dir/bikemap.db`` AND ``data_dir/lts-network.geojson.gz`` on
each call. Per Plan 2D §5.7 these MUST move together: the routing engine
reads bikemap.db (snapshot loaded at startup) and the Explorer view at
``/explore`` reads lts-network.geojson.gz (re-fetched per request). A
bikemap.db newer than the geojson surfaces as data-skew on Explorer; a
geojson newer than bikemap.db hides routing-relevant edges from the map.

Atomicity contract
==================
1. Both uploads stream to ``<dest>.new`` tempfiles in ``data_dir`` (same
   filesystem → ``os.replace`` is atomic).
2. Both tempfiles are validated (bikemap.db must declare
   ``schema_version >= MIN_SCHEMA_VERSION``; geojson.gz must be non-empty
   and gzip-magic) BEFORE either replace runs.
3. The two ``os.replace`` calls happen back-to-back. POSIX has no
   multi-file transaction primitive: if the second ``os.replace`` fails
   after the first succeeded (e.g., disk failure mid-operation), the
   response is 5xx and the operator MUST re-run ``make upload-db`` to
   restore the invariant. The retry is idempotent — it overwrites both.

Token gating
============
The endpoint is wired into the Flask app only when ``UPLOAD_TOKEN`` is set
(see ``app/main.py``). When wired, every request requires
``Authorization: Bearer <UPLOAD_TOKEN>``; ``hmac.compare_digest`` is used
to foil timing attacks.

Service reload caveat
=====================
The running gunicorn worker holds the old graph in memory (loaded at
startup); the new bikemap.db doesn't take effect until the service
restarts. The geojson is re-read per request via ``send_from_directory``,
so Explorer picks up the new file immediately — which is precisely why
the two files MUST move together. Operator triggers a Render redeploy
after a successful upload.
"""
from __future__ import annotations

import hmac
import os
import sqlite3
from pathlib import Path

from flask import Blueprint, Response, jsonify, request

# Must match app.main.MIN_SCHEMA_VERSION. Kept duplicated rather than
# imported because importing from app.main would create a cycle
# (main → routes.admin → main).
MIN_SCHEMA_VERSION = 2

BIKEMAP_FILENAME = "bikemap.db"
GEOJSON_FILENAME = "lts-network.geojson.gz"

# Multipart field names accepted on the POST body.
BIKEMAP_FIELD = "bikemap"
GEOJSON_FIELD = "lts_network"


def _validate_uploaded_bikemap(path: Path) -> str | None:
    """Returns an error message if the candidate DB is unusable, else None."""
    if path.stat().st_size == 0:
        return "uploaded bikemap.db is empty"
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        return f"uploaded bikemap.db is not a valid sqlite file: {exc}"
    try:
        try:
            row = con.execute(
                "SELECT schema_version FROM schema_meta LIMIT 1"
            ).fetchone()
        except sqlite3.Error as exc:
            return f"uploaded bikemap.db has no readable schema_meta: {exc}"
    finally:
        con.close()
    if row is None:
        return "uploaded bikemap.db schema_meta is empty"
    try:
        schema_version = int(row[0])
    except (TypeError, ValueError):
        return f"uploaded bikemap.db schema_version is not an int: {row[0]!r}"
    if schema_version < MIN_SCHEMA_VERSION:
        return (
            f"uploaded bikemap.db schema_version={schema_version} < "
            f"MIN_SCHEMA_VERSION={MIN_SCHEMA_VERSION}"
        )
    return None


def _validate_uploaded_geojson(path: Path) -> str | None:
    """Returns an error message if the geojson.gz is unusable, else None."""
    if path.stat().st_size == 0:
        return "uploaded lts-network.geojson.gz is empty"
    with open(path, "rb") as fp:
        magic = fp.read(2)
    if magic != b"\x1f\x8b":
        return "uploaded lts-network.geojson.gz is not gzipped"
    return None


def _tempfile_for(dest: Path) -> Path:
    return dest.parent / f"{dest.name}.new"


def build_admin_blueprint(*, data_dir: Path, upload_token: str) -> Blueprint:
    """Construct the admin blueprint.

    Callers decide whether to expose the endpoint (i.e. ``UPLOAD_TOKEN``
    env var was set). When the env var is unset the blueprint is simply
    not registered, so unauthenticated callers get Flask's default 404.
    """
    data_dir = data_dir.resolve()
    bikemap_dest = data_dir / BIKEMAP_FILENAME
    geojson_dest = data_dir / GEOJSON_FILENAME

    bp = Blueprint("admin", __name__)

    @bp.post("/admin/upload-bikemap-db")
    def upload_bikemap_db() -> Response | tuple[Response, int]:
        # Reject before consuming a multi-MB upload body.
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return jsonify({"error": "missing bearer token"}), 401
        if not hmac.compare_digest(auth[len("Bearer "):], upload_token):
            return jsonify({"error": "invalid token"}), 401

        if BIKEMAP_FIELD not in request.files:
            return jsonify(
                {"error": f"missing multipart field '{BIKEMAP_FIELD}'"},
            ), 400
        if GEOJSON_FIELD not in request.files:
            return jsonify(
                {"error": f"missing multipart field '{GEOJSON_FIELD}'"},
            ), 400

        bikemap_tmp = _tempfile_for(bikemap_dest)
        geojson_tmp = _tempfile_for(geojson_dest)
        try:
            request.files[BIKEMAP_FIELD].save(str(bikemap_tmp))
            request.files[GEOJSON_FIELD].save(str(geojson_tmp))

            err = _validate_uploaded_bikemap(bikemap_tmp)
            if err is not None:
                return jsonify({"error": err}), 400
            err = _validate_uploaded_geojson(geojson_tmp)
            if err is not None:
                return jsonify({"error": err}), 400

            # Each os.replace is atomic on its own; the pair is not
            # transactional. See module docstring.
            os.replace(bikemap_tmp, bikemap_dest)
            os.replace(geojson_tmp, geojson_dest)
        finally:
            # No-op on the success path (replace consumed both sources);
            # cleans tempfiles on any error path above.
            bikemap_tmp.unlink(missing_ok=True)
            geojson_tmp.unlink(missing_ok=True)

        return jsonify({
            "status": "ok",
            "bikemap_bytes": bikemap_dest.stat().st_size,
            "geojson_bytes": geojson_dest.stat().st_size,
            "note": (
                "service restart required for the new bikemap.db to be "
                "loaded into memory"
            ),
        })

    return bp
