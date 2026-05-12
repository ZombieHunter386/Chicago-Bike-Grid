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
        params={"q": address, "format": "json", "limit": str(limit), "countrycodes": "us"},
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
