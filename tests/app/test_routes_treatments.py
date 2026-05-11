"""Tests for /treatments/:slug route."""
from pathlib import Path

import pytest


@pytest.fixture
def treatments_app(tiny_bikemap_db: Path):
    """Build a Flask app with /treatments wired against the synthetic DB."""
    from flask import Flask

    from app.routes.treatments import build_treatments_blueprint

    # Seed a treatment row directly into the synthetic DB.
    from prep.db.builder import DbBuilder
    builder = DbBuilder(tiny_bikemap_db)
    builder.insert_treatments([(
        "neighborhood-greenway",
        "neighborhood-greenway",
        "ward-44",
        41.94, -87.67,
        None,
        "https://example.com/source",
        "Brief summary",
        "# Neighborhood greenway\n\nFull markdown body.",
    )])
    builder.close()

    app = Flask(__name__)
    app.register_blueprint(build_treatments_blueprint(tiny_bikemap_db))
    return app


def test_treatments_returns_treatment_by_slug(treatments_app) -> None:
    client = treatments_app.test_client()
    resp = client.get("/treatments/neighborhood-greenway")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["slug"] == "neighborhood-greenway"
    assert data["summary"] == "Brief summary"
    assert "markdown" in data
    assert "# Neighborhood greenway" in data["markdown"]


def test_treatments_404_for_unknown_slug(treatments_app) -> None:
    client = treatments_app.test_client()
    resp = client.get("/treatments/does-not-exist")
    assert resp.status_code == 404
