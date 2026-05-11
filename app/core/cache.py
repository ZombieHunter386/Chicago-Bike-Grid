"""Gap-analysis result cache (spec §3.5).

A separate writable SQLite DB (`cache.db`) so bikemap.db stays strictly
read-only in production. Cache keys are SHA-256 of rounded coordinates +
tier (privacy: raw addresses never persisted).

LRU eviction: if cache.db exceeds 500 MB, delete oldest entries until
size drops below 400 MB. Implemented synchronously; an async eviction
worker is a deferred optimization (spec §3.5 TODO).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

CACHE_SIZE_HIGH_BYTES = 500 * 1024 * 1024
CACHE_SIZE_LOW_BYTES = 400 * 1024 * 1024

_SCHEMA = """
CREATE TABLE IF NOT EXISTS gap_cache (
    key TEXT PRIMARY KEY,
    result_json TEXT NOT NULL,
    computed_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS cache_meta (
    bikemap_fingerprint TEXT PRIMARY KEY
);
"""


def cache_key(home: tuple[float, float], dest: tuple[float, float], tier: str) -> str:
    """SHA-256 of rounded(5-dec) coords + tier. Privacy-preserving (spec §3.5).
    home and dest are (lat, lon) tuples."""
    payload = (
        f"{round(home[0], 5)},{round(home[1], 5)}|"
        f"{round(dest[0], 5)},{round(dest[1], 5)}|"
        f"{tier}"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def bikemap_fingerprint(db_path: Path) -> str:
    """Stable fingerprint of bikemap.db: schema_version + sum of record counts.
    Used to detect when a new bikemap.db has been deployed (spec §3.5)."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        sv = con.execute("SELECT schema_version FROM schema_meta LIMIT 1").fetchone()
        schema_version = sv[0] if sv else "unknown"
        # Sum of all per-source record counts — captures data refreshes too.
        rows = con.execute("SELECT source, record_count FROM meta").fetchall()
        rc_sum = sum(int(r[1]) for r in rows)
    finally:
        con.close()
    return f"v{schema_version}-rc{rc_sum}"


def init_cache_db(cache_path: Path, fingerprint: str) -> None:
    """Create cache.db if missing; truncate if stored fingerprint != current."""
    con = sqlite3.connect(cache_path)
    try:
        con.executescript(_SCHEMA)
        row = con.execute("SELECT bikemap_fingerprint FROM cache_meta LIMIT 1").fetchone()
        stored = row[0] if row else None
        if stored != fingerprint:
            con.execute("DELETE FROM gap_cache")
            con.execute("DELETE FROM cache_meta")
            con.execute("INSERT INTO cache_meta (bikemap_fingerprint) VALUES (?)",
                        (fingerprint,))
            con.commit()
    finally:
        con.close()


def get_cached_gap(cache_path: Path, key: str) -> dict | None:
    if not cache_path.exists():
        return None
    con = sqlite3.connect(f"file:{cache_path}?mode=ro", uri=True)
    try:
        row = con.execute(
            "SELECT result_json FROM gap_cache WHERE key = ?", (key,)
        ).fetchone()
    finally:
        con.close()
    if row is None:
        return None
    return json.loads(row[0])


def put_cached_gap(cache_path: Path, key: str, result: dict) -> None:
    """Insert or replace cache entry. computed_at = current time (unix sec).
    Triggers LRU eviction if cache.db exceeds CACHE_SIZE_HIGH_BYTES."""
    import time
    con = sqlite3.connect(cache_path)
    try:
        con.execute(
            "INSERT OR REPLACE INTO gap_cache (key, result_json, computed_at) "
            "VALUES (?, ?, ?)",
            (key, json.dumps(result), int(time.time())),
        )
        con.commit()
    finally:
        con.close()
    # Synchronous eviction check. Spec §3.5 prefers async; deferred.
    if cache_path.stat().st_size > CACHE_SIZE_HIGH_BYTES:
        _evict_lru(cache_path)


def _evict_lru(cache_path: Path) -> None:
    """Delete oldest gap_cache rows in batches until size drops below
    CACHE_SIZE_LOW_BYTES. VACUUM runs once at the end (Fix 11) — VACUUM
    rewrites the entire DB, so calling it per batch multiplies wall-clock
    cost by the number of iterations.

    Note: SQLite's reported file size doesn't shrink until VACUUM, so we
    estimate post-eviction size from row count and avoid the size loop.

    Approximation caveat (Fix D): rows-to-drop is estimated from average
    row size; variance can leave the post-VACUUM file slightly above
    CACHE_SIZE_LOW_BYTES, in which case the next write triggers a tiny
    follow-up eviction. Converges; over-eviction by a few rows is benign.
    """
    target_bytes = CACHE_SIZE_LOW_BYTES
    con = sqlite3.connect(cache_path)
    try:
        # Approximate average row size to estimate how many to delete.
        cur = con.execute("SELECT COUNT(*), COALESCE(SUM(LENGTH(result_json)), 0) FROM gap_cache")
        n_rows, total_json_bytes = cur.fetchone()
        if n_rows == 0:
            return
        # Add ~50 bytes/row for key + computed_at + index overhead.
        est_avg_row = (total_json_bytes / n_rows) + 50
        current_bytes = cache_path.stat().st_size
        bytes_to_drop = max(0, current_bytes - target_bytes)
        rows_to_drop = int(bytes_to_drop / est_avg_row) + 1
        if rows_to_drop <= 0:
            return
        con.execute(
            "DELETE FROM gap_cache WHERE key IN ("
            "  SELECT key FROM gap_cache ORDER BY computed_at ASC LIMIT ?"
            ")",
            (rows_to_drop,),
        )
        con.commit()
        con.execute("VACUUM")
    finally:
        con.close()
