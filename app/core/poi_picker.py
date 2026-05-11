"""POI loading and nearest-by-category lookup (spec §3.6).

Loaded once at startup; ~3,300 POIs across 7+ categories ≈ 700 KB resident.
Linear scan per category for nearest-of-category queries is fine at this
scale.

Fix 10: Poi.x_m / y_m (EPSG:6454 projected coords) are precomputed at
load. nearest_poi avoids per-call pyproj transform calls — drops query
from ~23ms to <1ms at typical category sizes.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from pyproj import Transformer
from shapely import wkb

_TO_IL_EAST_M = Transformer.from_crs("EPSG:4326", "EPSG:6454", always_xy=True).transform


@dataclass(frozen=True)
class Poi:
    poi_id: int
    name: str | None
    address: str | None
    category: str
    source: str
    lat: float
    lon: float
    x_m: float           # EPSG:6454 metres (Fix 10 — precomputed)
    y_m: float


def load_pois(db_path: Path) -> dict[str, list[Poi]]:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    out: dict[str, list[Poi]] = {}
    for r in con.execute("SELECT id, name, address, category, source, geom FROM pois"):
        pt = wkb.loads(r["geom"])
        x_m, y_m = _TO_IL_EAST_M(pt.x, pt.y)
        out.setdefault(r["category"], []).append(Poi(
            poi_id=r["id"],
            name=r["name"],
            address=r["address"],
            category=r["category"],
            source=r["source"],
            lat=pt.y,
            lon=pt.x,
            x_m=x_m,
            y_m=y_m,
        ))
    con.close()
    return out


def nearest_poi(pois: list[Poi], lat: float, lon: float) -> Poi | None:
    """Return the POI nearest to (lat, lon) by crow-flies distance.
    Returns None if `pois` is empty. Uses precomputed Poi.x_m/y_m to avoid
    per-call projection overhead."""
    if not pois:
        return None
    qx, qy = _TO_IL_EAST_M(lon, lat)
    best: Poi | None = None
    best_d2 = float("inf")
    for p in pois:
        d2 = (p.x_m - qx) ** 2 + (p.y_m - qy) ** 2
        if d2 < best_d2:
            best_d2 = d2
            best = p
    return best
