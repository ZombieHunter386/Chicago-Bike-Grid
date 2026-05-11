"""POST /pois — find the nearest POI of a given category.

Coordinates accepted only via POST JSON body (spec §3.8).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from app.core.poi_picker import Poi, nearest_poi


def build_pois_blueprint(pois_by_category: dict[str, list[Poi]]) -> Blueprint:
    bp = Blueprint("pois", __name__)

    @bp.post("/pois")
    def find_poi():
        body = request.get_json(silent=True) or {}
        near = body.get("near") or {}
        cat = body.get("category")
        try:
            lat = float(near["lat"])
            lon = float(near["lon"])
        except (KeyError, TypeError, ValueError):
            return jsonify({"error": "invalid 'near': expected {lat, lon}"}), 400
        if not isinstance(cat, str):
            return jsonify({"error": "missing 'category'"}), 400
        pois = pois_by_category.get(cat)
        if not pois:
            return jsonify({"error": f"no POIs in category '{cat}'"}), 404
        p = nearest_poi(pois, lat, lon)
        if p is None:
            return jsonify({"error": "no POI found"}), 404
        return jsonify({
            "poi_id": p.poi_id,
            "name": p.name,
            "address": p.address,
            "category": p.category,
            "source": p.source,
            "lat": p.lat,
            "lon": p.lon,
        })

    return bp
