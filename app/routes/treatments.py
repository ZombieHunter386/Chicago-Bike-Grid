"""GET /treatments/:slug — serve markdown library entries.

Loaded once at blueprint construction; ~5 entries in current data.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from flask import Blueprint, jsonify


def _load_treatments(db_path: Path) -> dict[str, dict]:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT slug, type, ward, location_lat, location_lng, "
        "photo_path, source_url, summary, body_md FROM treatments"
    ).fetchall()
    con.close()
    return {
        r["slug"]: {
            "slug": r["slug"],
            "type": r["type"],
            "ward": r["ward"],
            "location": (
                {"lat": r["location_lat"], "lon": r["location_lng"]}
                if r["location_lat"] is not None else None
            ),
            "photo_path": r["photo_path"],
            "source_url": r["source_url"],
            "summary": r["summary"],
            "markdown": r["body_md"],
        }
        for r in rows
    }


def build_treatments_blueprint(db_path: Path) -> Blueprint:
    """Construct the treatments blueprint, eagerly loading the table into memory."""
    treatments = _load_treatments(db_path)
    bp = Blueprint("treatments", __name__)

    @bp.get("/treatments/<slug>")
    def get_treatment(slug: str):
        t = treatments.get(slug)
        if t is None:
            return jsonify({"error": "treatment not found"}), 404
        return jsonify(t)

    return bp
