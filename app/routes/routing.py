"""POST /routes — fast + safe routes for one home→destination pair.

Coordinates accepted only via POST JSON body (spec §3.8).
Inputs that snap to a vertex >5 km away are rejected with 400 (Fix 8).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from app.core.graph import GraphSnapshot, nearest_vertex
from app.core.routing import Route, compute_fast_route, compute_safe_route
from app.core.weights import TIERS

# Reject inputs whose nearest vertex is more than this far away (likely
# outside Cook County). 5 km is generous — a legitimate Chicago address
# should snap to within a few hundred metres.
MAX_SNAP_DISTANCE_M = 5000.0


def _route_to_payload(snap: GraphSnapshot, r: Route | None) -> dict | None:
    if r is None:
        return None
    # Cast numpy float64 scalars to Python floats so jsonify produces clean
    # JSON numbers (some encoders choke on np scalars).
    polyline = [
        {"lat": float(snap.vertex_coords_wgs84[v][0]),
         "lon": float(snap.vertex_coords_wgs84[v][1])}
        for v in r.vertex_path
    ]
    # polyline_lts[i] is the effective LTS of the segment connecting
    # polyline[i] → polyline[i+1]. Length is len(polyline) - 1. Frontend
    # uses this to color the safe route green-on-tier / amber-off-tier
    # per segment (per spec §2.2).
    return {
        "polyline": polyline,
        "polyline_lts": list(r.edge_lts),
        "length_m": r.length_m,
        "is_fallback": r.is_fallback,
        "lts_distribution": r.lts_distribution,
    }


def build_routes_blueprint(snap: GraphSnapshot) -> Blueprint:
    bp = Blueprint("routes", __name__)

    @bp.post("/routes")
    def routes():
        body = request.get_json(silent=True) or {}
        home = body.get("home") or {}
        dest = body.get("dest") or {}
        tier = body.get("tier")
        try:
            h_lat = float(home["lat"])
            h_lon = float(home["lon"])
            d_lat = float(dest["lat"])
            d_lon = float(dest["lon"])
        except (KeyError, TypeError, ValueError):
            return jsonify({"error": "invalid 'home' or 'dest'"}), 400
        if tier not in TIERS:
            return jsonify({"error": f"invalid tier '{tier}'"}), 400

        src_v, src_dist = nearest_vertex(snap, h_lat, h_lon)
        dst_v, dst_dist = nearest_vertex(snap, d_lat, d_lon)
        if src_dist > MAX_SNAP_DISTANCE_M or dst_dist > MAX_SNAP_DISTANCE_M:
            return jsonify({
                "error": "home or dest is outside the graph's extent (too far from any intersection)",
            }), 400

        fast = compute_fast_route(snap, src_v, dst_v)
        safe = compute_safe_route(snap, src_v, dst_v, tier)

        return jsonify({
            "fast": _route_to_payload(snap, fast),
            "safe": _route_to_payload(snap, safe),
        })

    return bp
