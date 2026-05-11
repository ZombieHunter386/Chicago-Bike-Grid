# prep/db/builder.py
from __future__ import annotations

import datetime as dt
import sqlite3
from collections.abc import Iterable
from pathlib import Path

from pyproj import Transformer
from shapely import wkb, wkt
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform

from prep.joins.hin_to_osm import (
    HinIntersectionFeature,
    HinIntersectionMatch,
    HinSegmentFeature,
    HinSegmentMatch,
)
from prep.lts.ingest import IntersectionRecord, PoiRecord, SegmentRecord

SCHEMA_VERSION = 2  # bumped: streets PK road_id, head/tail use PFB intersection ids
SCHEMA_SQL_PATH = Path(__file__).parent / "schema.sql"

# WGS84 → NAD83(2011) Illinois East (metres) for accurate metric distance math.
# Per spec §3.2 (corrected) and matching prep/joins/hin_to_osm.py.
_TO_IL_EAST_M = Transformer.from_crs("EPSG:4326", "EPSG:6454", always_xy=True).transform


def _length_meters(g: BaseGeometry) -> float:
    return transform(_TO_IL_EAST_M, g).length


def _to_wkb(g: BaseGeometry) -> bytes:
    """Serialize a shapely geometry to standard binary WKB."""
    return wkb.dumps(g)


def bytes_to_wkt(blob: bytes) -> str:
    """Test helper: read stored WKB and return WKT for inspection."""
    return wkb.loads(blob).wkt


class DbBuilder:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.conn: sqlite3.Connection | None = None

    def _conn(self) -> sqlite3.Connection:
        if self.conn is None:
            self.conn = sqlite3.connect(self.db_path)
            self.conn.execute("PRAGMA foreign_keys = ON")
        return self.conn

    def create_schema(self) -> None:
        sql = SCHEMA_SQL_PATH.read_text()
        self._conn().executescript(sql)

    def insert_streets(
        self,
        segments: Iterable[SegmentRecord],
        hin_matches: dict[int, HinSegmentMatch] | None = None,
    ) -> int:
        """Insert street segments.

        Streets are keyed on PFB ROAD_ID (unique per segment); head/tail
        intersection IDs come directly from each SegmentRecord. HIN matches
        are looked up by road_id (HIN-to-OSM joiner now matches per-block).
        """
        hin_matches = hin_matches or {}
        rows = []
        for s in segments:
            geom = wkt.loads(s.geometry_wkt)
            length = _length_meters(geom)
            m = hin_matches.get(s.road_id)
            rows.append((
                s.road_id,
                s.osm_id,
                s.name,
                _to_wkb(geom),
                s.head_int_id,
                s.tail_int_id,
                length,
                s.lts,
                s.highway,
                s.speed,
                1 if m else 0,
                1 if m and m.modal_flags.get("bike") else 0,
                1 if m and m.modal_flags.get("ped") else 0,
                m.severity_rank if m else None,
            ))
        cur = self._conn().executemany(
            "INSERT OR REPLACE INTO streets "
            "(road_id, osm_id, name, geom, head_node_osm_id, tail_node_osm_id, length_m, "
            "lts, highway, speed, on_hin, hin_modal_bike, hin_modal_ped, hin_severity_rank) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        self._conn().commit()
        return cur.rowcount

    def insert_intersections(
        self,
        intersections: Iterable[IntersectionRecord],
        hin_matches: dict[int, HinIntersectionMatch] | None = None,
    ) -> int:
        hin_matches = hin_matches or {}
        rows = []
        for i in intersections:
            geom = wkt.loads(i.geometry_wkt)
            m = hin_matches.get(i.osm_id)
            rows.append((
                i.osm_id,
                _to_wkb(geom),
                i.lts_approach,
                1 if i.signalized else (0 if i.signalized is False else None),
                i.lanes_crossed,
                1 if m else 0,
                1 if m and m.modal_flags.get("bike") else 0,
                1 if m and m.modal_flags.get("ped") else 0,
                m.severity_rank if m else None,
            ))
        cur = self._conn().executemany(
            "INSERT OR REPLACE INTO intersections "
            "(osm_id, geom, lts_approach, signalized, lanes_crossed, "
            "on_hin, hin_modal_bike, hin_modal_ped, hin_severity_rank) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            rows,
        )
        self._conn().commit()
        return cur.rowcount

    def insert_pois(self, pois: Iterable[PoiRecord]) -> int:
        rows = [
            (p.name, p.address, p.category, p.source, _to_wkb(wkt.loads(p.geometry_wkt)))
            for p in pois
        ]
        cur = self._conn().executemany(
            "INSERT INTO pois (name, address, category, source, geom) "
            "VALUES (?,?,?,?,?)",
            rows,
        )
        self._conn().commit()
        return cur.rowcount

    def insert_hin_features(
        self,
        seg_features: Iterable[HinSegmentFeature],
        int_features: Iterable[HinIntersectionFeature],
    ) -> int:
        """Mirror raw HIN features into the hin_features table.

        Stores feature_id, kind ('segment'|'intersection'), modal flags, severity
        rank, and original geometry as WKB. Used for debugging/cross-reference.
        """
        rows = []
        for sf in seg_features:
            rows.append((
                sf.feature_id,
                "segment",
                1 if sf.modal_flags.get("bike") else 0,
                1 if sf.modal_flags.get("ped") else 0,
                sf.severity_rank,
                _to_wkb(sf.geometry),
            ))
        for inf in int_features:
            rows.append((
                inf.feature_id,
                "intersection",
                1 if inf.modal_flags.get("bike") else 0,
                1 if inf.modal_flags.get("ped") else 0,
                inf.severity_rank,
                _to_wkb(inf.geometry),
            ))
        cur = self._conn().executemany(
            "INSERT OR REPLACE INTO hin_features "
            "(feature_id, kind, modal_bike, modal_ped, severity_rank, source_geom) "
            "VALUES (?,?,?,?,?,?)",
            rows,
        )
        self._conn().commit()
        return cur.rowcount

    def insert_treatments(
        self,
        rows: Iterable[tuple],  # type: ignore[type-arg]
    ) -> int:
        """Insert treatment rows (tuples) into the treatments table.

        Each row must be: (slug, type, ward, location_lat, location_lng,
        photo_path, source_url, summary, body_md). The treatments_loader
        parses markdown and constructs these tuples — this method is the
        single write entry point for the treatments table (no private DB
        access from outside DbBuilder).
        """
        row_seq = list(rows)
        cur = self._conn().executemany(
            "INSERT OR REPLACE INTO treatments "
            "(slug, type, ward, location_lat, location_lng, photo_path, source_url, summary, body_md) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            row_seq,
        )
        self._conn().commit()
        return cur.rowcount

    def record_meta(self, source: str, record_count: int, status: str) -> None:
        self._conn().execute(
            "INSERT OR REPLACE INTO meta (source, last_refresh, record_count, status) "
            "VALUES (?,?,?,?)",
            (source, dt.datetime.now(dt.UTC).isoformat(), record_count, status),
        )
        self._conn().commit()

    def record_schema_meta(self, code_version: str) -> None:
        self._conn().execute("DELETE FROM schema_meta")
        self._conn().execute(
            "INSERT INTO schema_meta (schema_version, built_at, code_version) VALUES (?,?,?)",
            (SCHEMA_VERSION, dt.datetime.now(dt.UTC).isoformat(), code_version),
        )
        self._conn().commit()

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()
            self.conn = None
