"""POST /gap-analysis + GET /gap-analysis/status — async gap computation.

Cache hit returns {status: ready, result} immediately. Cache miss submits
a job to a 3-worker thread pool, returns 202 with {status: running, job_id}.
Client polls /gap-analysis/status?job= every 1.5s (per spec §3.5).
"""
from __future__ import annotations

import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from flask import Blueprint, jsonify, request
from flask_limiter import Limiter

from app.core.cache import cache_key, get_cached_gap, put_cached_gap
from app.core.gap_analysis import GapResult, analyze_gap
from app.core.graph import GraphSnapshot, nearest_vertex
from app.core.weights import TIERS

JOB_TTL_S = 600  # drop completed/failed futures after 10 minutes


def _serialize(result: GapResult, snap: GraphSnapshot) -> dict[str, Any]:
    """Make GapResult JSON-friendly. Includes route polylines (Fix B) so the
    frontend doesn't need a second /routes call to draw the routes."""

    def _route_dict(r) -> dict | None:
        if r is None:
            return None
        # vertex_coords_wgs84 is a numpy (V, 2) array; cast scalars to Python
        # floats for clean JSON output.
        polyline = [
            {"lat": float(snap.vertex_coords_wgs84[v][0]),
             "lon": float(snap.vertex_coords_wgs84[v][1])}
            for v in r.vertex_path
        ]
        return {
            "polyline": polyline,
            "polyline_lts": list(r.edge_lts),
            "edge_count": len(r.edge_path),
            "length_m": r.length_m,
            "is_fallback": r.is_fallback,
            "lts_distribution": r.lts_distribution,
        }

    def _to_dict(c) -> dict:
        return asdict(c) if is_dataclass(c) and not isinstance(c, type) else dict(c)

    return {
        "fast_route": _route_dict(result.fast_route),
        "safe_route": _route_dict(result.safe_route),
        "safe_route_is_fallback": result.safe_route_is_fallback,
        # D' corridor framing (spec §4.5): single advocacy ask + per-road
        # marginals + separate danger-intersection group. Frontend renders
        # the overlay polyline from corridor.fast_lts_overlay_wkt.
        "corridor": _to_dict(result.corridor) if result.corridor else None,
        "intersections": [_to_dict(i) for i in result.intersections],
    }


def build_gap_analysis_blueprint(
    snap: GraphSnapshot, cache_db: Path, limiter: Limiter,
) -> Blueprint:
    bp = Blueprint("gap_analysis", __name__)
    executor = ThreadPoolExecutor(max_workers=3)
    # Keyed by cache_key (NOT a random uuid) so duplicate POSTs from the
    # same client (or different clients) for the same (home, dest, tier)
    # reuse one in-flight Future. Previously every POST queued a fresh
    # worker job; on a state-mutation storm (drilldown enter/exit, tier
    # toggle, etc.) the executor's 3-worker pool got swamped with duplicate
    # work, slowed all responses, and burnt through the 10-req/min rate
    # limit budget pointlessly. Externally the cache_key doubles as the
    # job_id — opaque hex string, fine for a polling identifier.
    jobs: dict[str, tuple[Future, float]] = {}  # cache_key -> (future, submitted_at)

    def _gc_jobs() -> None:
        """Drop stale completed/failed futures."""
        now = time.time()
        for jid in list(jobs.keys()):
            fut, submitted = jobs[jid]
            if fut.done() and (now - submitted) > JOB_TTL_S:
                jobs.pop(jid, None)

    def _compute(home: tuple[float, float], dest: tuple[float, float], tier: str) -> dict:
        src_v, _ = nearest_vertex(snap, *home)
        dst_v, _ = nearest_vertex(snap, *dest)
        result = analyze_gap(snap, src_v, dst_v, tier)
        payload = _serialize(result, snap)
        put_cached_gap(cache_db, cache_key(home, dest, tier), payload)
        return payload

    @bp.post("/gap-analysis")
    @limiter.limit("10 per minute")
    def submit():
        body = request.get_json(silent=True) or {}
        home = body.get("home") or {}
        dest = body.get("dest") or {}
        tier = body.get("tier")
        try:
            h = (float(home["lat"]), float(home["lon"]))
            d = (float(dest["lat"]), float(dest["lon"]))
        except (KeyError, TypeError, ValueError):
            return jsonify({"error": "invalid 'home' or 'dest'"}), 400
        if tier not in TIERS:
            return jsonify({"error": f"invalid tier '{tier}'"}), 400

        # Snap-distance check (Fix 8).
        _, h_dist = nearest_vertex(snap, *h)
        _, d_dist = nearest_vertex(snap, *d)
        if h_dist > 5000.0 or d_dist > 5000.0:
            return jsonify({
                "error": "home or dest is outside the graph's extent",
            }), 400

        key = cache_key(h, d, tier)
        cached = get_cached_gap(cache_db, key)
        if cached is not None:
            return jsonify({"status": "ready", "result": cached}), 200

        _gc_jobs()
        # Dedupe: if a future for this key is already in flight, return its
        # job_id rather than submitting a duplicate. Identical (home, dest,
        # tier) → identical work → one shared Future.
        existing = jobs.get(key)
        if existing is not None and not existing[0].done():
            return jsonify({"status": "running", "job_id": key}), 202

        fut = executor.submit(_compute, h, d, tier)
        jobs[key] = (fut, time.time())
        return jsonify({"status": "running", "job_id": key}), 202

    @bp.get("/gap-analysis/status")
    def status():
        job_id = request.args.get("job")
        if not job_id or job_id not in jobs:
            return jsonify({"error": "unknown job"}), 404
        fut, _ = jobs[job_id]
        if not fut.done():
            return jsonify({"status": "running", "job_id": job_id}), 200
        try:
            result = fut.result()
        except Exception as e:  # noqa: BLE001
            return jsonify({"status": "error", "error": str(e)}), 500
        return jsonify({"status": "ready", "result": result}), 200

    return bp
