"""POST /geocode — proxy address strings to Nominatim with self-throttling.

Address is sent to Nominatim (necessarily) but never written to our logs.
"""
from __future__ import annotations

import threading
import time
from typing import Any

import requests
from flask import Blueprint, jsonify, request

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
MIN_INTERVAL_S = 1.1  # Nominatim TOS

# Service-area bounding box, as Nominatim's "viewbox" = left,top,right,bottom
# (min_lng, max_lat, max_lng, min_lat). Combined with bounded=1, Nominatim
# restricts results to this box so suggestions stay in the service area
# instead of returning same-named streets from across the US.
#
# MUST track `target.bbox` in prep/config/sources.yaml — that bbox decides
# which streets exist in the routing graph, and geocoding outside it yields
# addresses the router cannot serve. Widened to all of Cook County with the
# 2026-07-30 expansion; while this still read the old Chicago-only box, every
# suburban address (Evanston, Oak Park, Schaumburg) failed to geocode.
# `test_geocode_viewbox_matches_target_bbox` pins the two together.
_SERVICE_AREA_VIEWBOX = "-88.2636,42.1543,-87.5240,41.4697"

_throttle_lock = threading.Lock()
_last_request_at = [0.0]


def _fetch_nominatim(address: str, user_agent: str, limit: int = 1) -> list[dict[str, Any]]:
    """Throttled Nominatim search. Internal seam; tests patch this."""
    with _throttle_lock:
        gap = time.monotonic() - _last_request_at[0]
        if gap < MIN_INTERVAL_S:
            time.sleep(MIN_INTERVAL_S - gap)
        _last_request_at[0] = time.monotonic()
    resp = requests.get(
        NOMINATIM_URL,
        params={
            "q": address,
            "format": "json",
            "limit": str(limit),
            "countrycodes": "us",
            "viewbox": _SERVICE_AREA_VIEWBOX,
            "bounded": "1",
        },
        headers={"User-Agent": user_agent},
        timeout=8,
    )
    resp.raise_for_status()
    return resp.json()


def build_geocode_blueprint(user_agent: str) -> Blueprint:
    bp = Blueprint("geocode", __name__)

    @bp.post("/geocode")
    def geocode():
        body = request.get_json(silent=True) or {}
        address = body.get("address")
        if not isinstance(address, str) or not address.strip():
            return jsonify({"error": "missing 'address'"}), 400
        try:
            results = _fetch_nominatim(address, user_agent)
        except requests.RequestException as e:
            return jsonify({"error": f"geocoder error: {e.__class__.__name__}"}), 502
        if not results:
            return jsonify({"error": "no results"}), 404
        first = results[0]
        return jsonify({
            "display_name": first.get("display_name"),
            "lat": float(first["lat"]),
            "lon": float(first["lon"]),
        })

    @bp.post("/geocode/suggest")
    def geocode_suggest():
        """Return up to 5 Nominatim matches for type-ahead autocomplete.

        Distinct endpoint from /geocode so the existing single-result shape
        (returned by `Set Location` and custom-destination form) stays put.
        Empty/short queries return [] without hitting Nominatim — typing
        one or two characters is rarely useful and would burn the 1s
        global throttle for no gain.
        """
        body = request.get_json(silent=True) or {}
        address = body.get("address")
        if not isinstance(address, str) or len(address.strip()) < 3:
            return jsonify({"results": []})
        try:
            results = _fetch_nominatim(address.strip(), user_agent, limit=5)
        except requests.RequestException as e:
            return jsonify({"error": f"geocoder error: {e.__class__.__name__}"}), 502
        suggestions = []
        for r in results:
            try:
                suggestions.append({
                    "display_name": r.get("display_name"),
                    "lat": float(r["lat"]),
                    "lon": float(r["lon"]),
                    "place_id": r.get("place_id"),
                })
            except (KeyError, TypeError, ValueError):
                continue  # skip malformed Nominatim row, don't fail the whole request
        return jsonify({"results": suggestions})

    return bp
