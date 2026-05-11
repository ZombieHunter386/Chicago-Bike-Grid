-- prep/db/cache_schema.sql
-- Schema for cache.db. Read-write at runtime; created on first cache miss.
-- Reset whenever bikemap.db changes (web service detects schema_version+record_count fingerprint mismatch).

CREATE TABLE IF NOT EXISTS cache_fingerprint (
    bikemap_schema_version INTEGER NOT NULL,
    bikemap_streets_count INTEGER NOT NULL,
    built_against_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS gap_cache (
    cache_key TEXT PRIMARY KEY,    -- SHA-256 of (home_coord_rounded, dest_coord_rounded, tier)
    result_json TEXT NOT NULL,
    computed_at TEXT NOT NULL,
    size_bytes INTEGER NOT NULL    -- for LRU eviction
);

CREATE INDEX IF NOT EXISTS idx_gap_cache_computed_at ON gap_cache(computed_at);
