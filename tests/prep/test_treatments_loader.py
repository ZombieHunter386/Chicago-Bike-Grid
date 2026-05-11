import sqlite3
from pathlib import Path

from prep.db.builder import DbBuilder
from prep.db.treatments_loader import load_treatments


def test_load_treatments_from_markdown_directory(tmp_path: Path) -> None:
    treatments_dir = tmp_path / "treatments"
    treatments_dir.mkdir()
    (treatments_dir / "pedestrian-refuge.md").write_text(
        """---
type: intersection_treatment
ward: 47
location_lat: 41.945
location_lng: -87.683
photo_path: photos/foster-refuge.jpg
source_url: https://example.com/refuge
summary: Concrete median refuge enabling two-stage crossings.
---

# Pedestrian Refuge Island

Concrete median that gives crossing pedestrians and cyclists a place to stop
in the middle of a wide street, breaking the crossing into two stages.

## Chicago example

Foster Ave & Hoyne Ave, Ward 47.
"""
    )
    (treatments_dir / "neighborhood-greenway.md").write_text(
        """---
type: corridor_treatment
ward: 1
summary: Calmed residential street prioritizing bicycle traffic.
---

# Neighborhood Greenway

A residential street with reduced auto traffic and added bike priority elements.
"""
    )

    db_path = tmp_path / "test.db"
    builder = DbBuilder(db_path)
    builder.create_schema()

    n = load_treatments(treatments_dir, builder)
    builder.close()
    assert n == 2

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT slug, type, ward, location_lat, location_lng, photo_path FROM treatments ORDER BY slug"
    ).fetchall()
    assert len(rows) == 2

    refuge = next(r for r in rows if r[0] == "pedestrian-refuge")
    assert refuge[1] == "intersection_treatment"
    assert refuge[2] == "47"
    assert refuge[3] == 41.945
    assert refuge[5] == "photos/foster-refuge.jpg"

    body = conn.execute(
        "SELECT body_md FROM treatments WHERE slug = ?", ("pedestrian-refuge",)
    ).fetchone()
    assert "Pedestrian Refuge Island" in body[0]
