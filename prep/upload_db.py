"""Push bikemap.db + lts-network.geojson.gz to a deployed instance.

Per Plan 2D §5.7 these MUST move together: the routing engine
(bikemap.db) and the Explorer view (lts-network.geojson.gz) need to
reflect the same prep run. The endpoint at ``/admin/upload-bikemap-db``
enforces the dual-file constraint server-side — this client just makes
sure we send both.

Invocation
==========
::

    export RENDER_BASE_URL=https://chicago-bike-advocacy-map.onrender.com
    export UPLOAD_TOKEN=<secret matching Render env var>
    python -m prep.upload_db                  # uses data/* defaults
    # or via the Makefile target:
    make upload-db

Override the local paths (rare) with::

    BIKEMAP_DB_LOCAL=path/to/bikemap.db \
    LTS_NETWORK_LOCAL=path/to/lts-network.geojson.gz \
    python -m prep.upload_db

Service restart
===============
A successful upload only replaces the files on disk. The running
gunicorn worker still holds the OLD graph in memory until the service
restarts. After ``upload-db`` succeeds, trigger a Render redeploy
(dashboard → Manual Deploy → "Deploy latest commit") so the new
bikemap.db is loaded.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import requests

DEFAULT_DB_PATH = Path("data/bikemap.db")
DEFAULT_GEOJSON_PATH = Path("data/lts-network.geojson.gz")
UPLOAD_PATH = "/admin/upload-bikemap-db"
# 50 MB bikemap.db + 6 MB geojson on a slow uplink: budget 10 minutes.
REQUEST_TIMEOUT_SECONDS = 600


def upload(
    *,
    db_path: Path,
    geojson_path: Path,
    base_url: str,
    token: str,
) -> dict:
    """POST both files as multipart. Raises ``requests.HTTPError`` on non-2xx
    so the Makefile target fails loudly."""
    db_mb = db_path.stat().st_size / 1e6
    gj_mb = geojson_path.stat().st_size / 1e6
    print(
        f"Uploading bikemap.db ({db_mb:.1f} MB) + "
        f"lts-network.geojson.gz ({gj_mb:.1f} MB) to {base_url}...",
        flush=True,
    )
    started = time.time()
    with open(db_path, "rb") as fdb, open(geojson_path, "rb") as fgj:
        resp = requests.post(
            f"{base_url.rstrip('/')}{UPLOAD_PATH}",
            files={
                "bikemap": (
                    db_path.name, fdb, "application/octet-stream",
                ),
                "lts_network": (
                    geojson_path.name, fgj, "application/gzip",
                ),
            },
            headers={"Authorization": f"Bearer {token}"},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    elapsed = time.time() - started
    if resp.status_code >= 400:
        # Print server-side error before raising so the operator sees the
        # actual cause (e.g., schema_version too old) in the failure log.
        print(f"  Server returned {resp.status_code}: {resp.text}", flush=True)
        resp.raise_for_status()
    payload = resp.json()
    print(f"  Done in {elapsed:.1f}s. Server reports: {payload}", flush=True)
    print(
        "NOTE: Trigger a Render redeploy so the new bikemap.db is loaded "
        "(Dashboard → Manual Deploy → 'Deploy latest commit').",
        flush=True,
    )
    return payload


def main() -> int:
    db_path = Path(os.environ.get("BIKEMAP_DB_LOCAL", str(DEFAULT_DB_PATH)))
    geojson_path = Path(
        os.environ.get("LTS_NETWORK_LOCAL", str(DEFAULT_GEOJSON_PATH)),
    )
    if not db_path.exists():
        print(f"error: missing {db_path}", file=sys.stderr)
        return 2
    if not geojson_path.exists():
        print(f"error: missing {geojson_path}", file=sys.stderr)
        return 2

    base_url = os.environ.get("RENDER_BASE_URL")
    if not base_url:
        print("error: RENDER_BASE_URL is not set", file=sys.stderr)
        return 2
    token = os.environ.get("UPLOAD_TOKEN")
    if not token:
        print("error: UPLOAD_TOKEN is not set", file=sys.stderr)
        return 2

    upload(
        db_path=db_path, geojson_path=geojson_path,
        base_url=base_url, token=token,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
