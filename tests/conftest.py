# tests/conftest.py
"""Root conftest. Shared fixtures live here so tests in any subpackage
(``tests/app``, ``tests/prep``) can use them without duplication.

Bikemap-DB fixtures (lifted from ``tests/app/conftest.py`` so the prep
exporter tests can reuse them — Plan 2D Task 1):

``tiny_bikemap_db`` — 5-node grid, used by simple unit tests (graph
loader, basic routing). Has an LTS-3 chokepoint at v300 to exercise the
max rule.

``divergent_bikemap_db`` — 4-node graph specifically designed to force
fast/safe divergence at 'parent' tier for gap-analysis tests. The fast
route uses a direct LTS-3 segment; the safe route detours via two
LTS-1 segments.

``tiny_bikemap_db_with_pois`` — ``tiny_bikemap_db`` plus a handful of
POIs across categories.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from prep.db.builder import DbBuilder
from prep.lts.ingest import IntersectionRecord, PoiRecord, SegmentRecord

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    """Path to the test fixtures directory."""
    return FIXTURES


@pytest.fixture
def cache_dir(tmp_path: Path) -> Path:
    """A clean cache directory per test."""
    d = tmp_path / "cache"
    d.mkdir()
    return d


@pytest.fixture
def output_dir(tmp_path: Path) -> Path:
    """A clean output directory per test."""
    d = tmp_path / "out"
    d.mkdir()
    return d


def _seg(road_id: int, osm_id: int, head: int, tail: int, lts: int,
         coords: list[tuple[float, float]],
         highway: str = "residential") -> SegmentRecord:
    line = "LINESTRING(" + ", ".join(f"{x} {y}" for x, y in coords) + ")"
    return SegmentRecord(
        road_id=road_id,
        osm_id=osm_id,
        head_int_id=head,
        tail_int_id=tail,
        name=f"Test St {road_id}",
        lts=lts,
        highway=highway,
        speed=25,
        ft_int_str=lts,
        tf_int_str=lts,
        geometry_wkt=line,
        raw_properties={},
    )


def _intersection(int_id: int, lat: float, lon: float, lts_approach: int) -> IntersectionRecord:
    return IntersectionRecord(
        osm_id=int_id,
        lts_approach=lts_approach,
        signalized=None,
        lanes_crossed=None,
        geometry_wkt=f"POINT ({lon} {lat})",
        raw_properties={},
    )


@pytest.fixture
def tiny_bikemap_db(tmp_path: Path) -> Path:
    """5-node grid for graph + routing unit tests.

         v200 (LTS-1 approach)
          |
     v100-v300-v400  (v300 lts_approach=3 — the chokepoint)
          |
         v500

    Streets (all bidirectional after load):
      r1 v100 ↔ v300  lts=1
      r2 v300 ↔ v400  lts=1
      r3 v200 ↔ v300  lts=2
      r4 v300 ↔ v500  lts=3
      r5 v100 ↔ v500  lts=3   (alternate cross-grid edge)
    """
    db_path = tmp_path / "bikemap.db"
    builder = DbBuilder(db_path)
    builder.create_schema()
    builder.insert_intersections([
        _intersection(100, 41.940, -87.680, 1),
        _intersection(200, 41.945, -87.675, 1),
        _intersection(300, 41.940, -87.675, 3),  # chokepoint at LTS-3 approach
        _intersection(400, 41.940, -87.670, 1),
        _intersection(500, 41.935, -87.675, 1),
    ])
    builder.insert_streets([
        _seg(1, 1001, 100, 300, 1, [(-87.680, 41.940), (-87.675, 41.940)]),
        _seg(2, 1002, 300, 400, 1, [(-87.675, 41.940), (-87.670, 41.940)]),
        _seg(3, 1003, 200, 300, 2, [(-87.675, 41.945), (-87.675, 41.940)]),
        _seg(4, 1004, 300, 500, 3, [(-87.675, 41.940), (-87.675, 41.935)]),
        _seg(5, 1005, 100, 500, 3, [(-87.680, 41.940), (-87.675, 41.935)]),
    ])
    builder.record_schema_meta(code_version="test")
    builder.close()
    return db_path


@pytest.fixture
def divergent_bikemap_db(tmp_path: Path) -> Path:
    """4-node graph where fast and safe routes diverge at 'parent' tier.

         v100 ────[r1: lts=1, len 150m]────── v200
          │                                    │
       [r3: lts=3, len 200m]              [r2: lts=1, len 150m]
          │                                    │
         v300 ────[skipping; v100→v300→v400 forces diagonal]
                                                │
                                              v400

    Simpler: v100 → v400 with two paths:
      Direct: r3 (LTS 3, 200m).
      Detour: r1 + r2 (LTS 1 + LTS 1, total 300m).

    All intersections have lts_approach=1 (no chokepoint at the nodes).
    Tier behavior at v100 → v400:
      Fast: r3 (200m, ignores LTS).
      Safe-any: r3 weighted 200×1.5=300; detour weighted 150+150=300.
                Either-or; igraph picks one. Test tolerates both.
      Safe-parent: r3 blocked (LTS 3); detour OK. Diverges from fast.
                   Gap candidate r3 (LTS 3); hypothesizing r3.lts=2 yields
                   weighted 200×1.2=240, beating detour's 300. New safe
                   route uses r3, length 200m. Old safe length was 300m,
                   savings 100m. Headline.feature_id = r3.road_id.
      Safe-kid: r3 blocked (LTS 3 > kid); detour LTS 1 OK. Same divergence.
    """
    db_path = tmp_path / "bikemap.db"
    builder = DbBuilder(db_path)
    builder.create_schema()
    builder.insert_intersections([
        _intersection(10, 41.940, -87.680, 1),  # v100
        _intersection(20, 41.945, -87.675, 1),  # v200
        _intersection(40, 41.940, -87.670, 1),  # v400
    ])
    builder.insert_streets([
        # r1: v100 ↔ v200, LTS 1, ~150m (NW direction)
        _seg(101, 2001, 10, 20, 1, [(-87.680, 41.940), (-87.675, 41.945)]),
        # r2: v200 ↔ v400, LTS 1, ~150m (SE direction)
        _seg(102, 2002, 20, 40, 1, [(-87.675, 41.945), (-87.670, 41.940)]),
        # r3: v100 ↔ v400 direct, LTS 3, ~200m (E direction along latitude line)
        _seg(103, 2003, 10, 40, 3, [(-87.680, 41.940), (-87.670, 41.940)],
             highway="residential"),
    ])
    builder.record_schema_meta(code_version="test")
    builder.close()
    return db_path


def _poi(name: str, category: str, lat: float, lon: float) -> PoiRecord:
    return PoiRecord(
        name=name,
        address=None,
        category=category,
        source="brokenspoke",
        geometry_wkt=f"POINT ({lon} {lat})",
        raw_properties={},
    )


@pytest.fixture
def tiny_bikemap_db_with_pois(tiny_bikemap_db: Path) -> Path:
    """tiny_bikemap_db plus a handful of POIs across categories."""
    builder = DbBuilder(tiny_bikemap_db)
    builder.insert_pois([
        _poi("Test Elementary", "school", 41.940, -87.671),    # near v400
        _poi("Far Elementary",  "school", 41.935, -87.675),    # near v500
        _poi("Test Park",       "park",   41.945, -87.675),    # near v200
        _poi("Solo Library",    "library", 41.940, -87.680),   # near v100
    ])
    builder.close()
    return tiny_bikemap_db
