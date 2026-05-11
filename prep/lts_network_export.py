"""Generates data/lts-network.geojson.gz from a built bikemap.db.

Run at the end of prep/main.run_pipeline so the static file ships with
the database (spec §5.3). Streams features through gzip so the
uncompressed JSON never lives in memory in its entirety.

Coordinates are rounded to 5 decimal places (~1 m at Chicago latitude).
PFB emits 7 decimals (~1 cm); the extra precision is invisible at the
zoom levels the Explorer view supports.
"""
from __future__ import annotations

import gzip
import json
import os
import sqlite3
from pathlib import Path

from shapely import wkb

_COORD_PRECISION = 5


def _round_coord(c: tuple[float, float]) -> list[float]:
    return [round(c[0], _COORD_PRECISION), round(c[1], _COORD_PRECISION)]


def export_lts_network(db_path: Path, output_path: Path) -> int:
    """Write a gzipped GeoJSON FeatureCollection of streets + intersections
    to ``output_path``. Returns the resulting file size in bytes.

    Writes atomically via ``<output_path>.tmp`` + ``os.replace``.
    """
    tmp_path = Path(str(output_path) + ".tmp")
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        with gzip.open(tmp_path, "wt", encoding="utf-8", compresslevel=6) as f:
            f.write('{"type":"FeatureCollection","features":[')
            first = True

            for r in con.execute(
                "SELECT lts, on_hin, geom FROM streets "
                "WHERE head_node_osm_id != tail_node_osm_id"
            ):
                line = wkb.loads(r["geom"])
                feature = {
                    "type": "Feature",
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [_round_coord(c) for c in line.coords],
                    },
                    "properties": {
                        "lts": int(r["lts"]),
                        "on_hin": bool(r["on_hin"]),
                    },
                }
                if not first:
                    f.write(",")
                else:
                    first = False
                f.write(json.dumps(feature, separators=(",", ":")))

            for r in con.execute(
                "SELECT lts_approach, on_hin, geom FROM intersections"
            ):
                pt = wkb.loads(r["geom"])
                feature = {
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": _round_coord((pt.x, pt.y)),
                    },
                    "properties": {
                        "lts_approach": int(r["lts_approach"]),
                        "on_hin": bool(r["on_hin"]),
                    },
                }
                if not first:
                    f.write(",")
                else:
                    first = False
                f.write(json.dumps(feature, separators=(",", ":")))

            f.write("]}")
    finally:
        con.close()

    os.replace(tmp_path, output_path)
    return output_path.stat().st_size
