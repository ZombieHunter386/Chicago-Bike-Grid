-- prep/db/schema.sql
-- Schema for bikemap.db. Read-only in production.
--
-- GEOMETRY STORAGE CONVENTION:
-- - Geometry columns store STANDARD BINARY WKB (Well-Known Binary) blobs in EPSG:4326.
-- - This format is interoperable: shapely.wkb.loads() reads it directly, and
--   SpatiaLite's RecoverGeometryColumn() can register these columns as spatial-indexed
--   Geometry columns at runtime (Plan 2's web service does this on startup).
-- - Plan 1 (this file) does NOT load SpatiaLite — we just write WKB. SpatiaLite-backed
--   spatial queries are a Plan 2 concern.
--
-- Distance math at runtime uses pyproj reprojection to EPSG:6454
-- (NAD83(2011) / Illinois East, metres) per spec §3.2.

PRAGMA foreign_keys = ON;

-- Schema versioning. Bump when any table changes shape.
-- Code must be backwards-compatible with the previous 2 schema versions (spec §3.11).
CREATE TABLE IF NOT EXISTS schema_meta (
    schema_version INTEGER NOT NULL,
    built_at TEXT NOT NULL,
    code_version TEXT
);

-- Per-source refresh metadata. One row per source per refresh.
CREATE TABLE IF NOT EXISTS meta (
    source TEXT PRIMARY KEY,
    last_refresh TEXT NOT NULL,
    record_count INTEGER NOT NULL,
    status TEXT NOT NULL  -- "OK" | "WARN" | "FAIL"
);

-- Street segments (edges of the routing graph).
--
-- KEYING NOTE: PFB's neighborhood_ways.shp emits one row per LTS-evaluation
-- block. PFB's ROAD_ID is unique per row; OSM_ID is many-to-one (one OSM way
-- can map to 100+ PFB rows). We use ROAD_ID as the primary key. osm_id is
-- retained as an indexed non-unique column for cross-referencing back to
-- the OSM way.
--
-- head_node_osm_id / tail_node_osm_id store PFB's intersection node IDs
-- (INTERSECTI / INTERSE_01), not OSM node IDs. The column names predate
-- the schema migration; they're kept to minimize diff churn.
CREATE TABLE IF NOT EXISTS streets (
    road_id INTEGER PRIMARY KEY,         -- PFB ROAD_ID
    osm_id INTEGER NOT NULL,             -- PFB OSM_ID (way ID; non-unique here)
    name TEXT,
    geom BLOB NOT NULL,                  -- WKB LineString (EPSG:4326)
    head_node_osm_id INTEGER NOT NULL,   -- PFB INTERSECTI (from-node)
    tail_node_osm_id INTEGER NOT NULL,   -- PFB INTERSE_01 (to-node)
    length_m REAL NOT NULL,
    lts INTEGER NOT NULL,                -- 1..3
    highway TEXT,
    speed INTEGER,
    on_hin INTEGER NOT NULL DEFAULT 0,           -- 0/1 boolean
    hin_modal_bike INTEGER NOT NULL DEFAULT 0,
    hin_modal_ped INTEGER NOT NULL DEFAULT 0,
    hin_severity_rank INTEGER
);

CREATE INDEX IF NOT EXISTS idx_streets_head ON streets(head_node_osm_id);
CREATE INDEX IF NOT EXISTS idx_streets_tail ON streets(tail_node_osm_id);
CREATE INDEX IF NOT EXISTS idx_streets_osm_id ON streets(osm_id);

-- Intersection nodes.
--
-- KEYING NOTE: osm_id stores PFB's intersection node ID (from
-- INTERSECTI / INTERSE_01). The column name is retained for diff continuity.
CREATE TABLE IF NOT EXISTS intersections (
    osm_id INTEGER PRIMARY KEY,          -- PFB intersection node ID
    geom BLOB NOT NULL,                  -- WKB Point (EPSG:4326)
    lts_approach INTEGER NOT NULL,       -- 1..3
    signalized INTEGER,
    lanes_crossed INTEGER,
    on_hin INTEGER NOT NULL DEFAULT 0,
    hin_modal_bike INTEGER NOT NULL DEFAULT 0,
    hin_modal_ped INTEGER NOT NULL DEFAULT 0,
    hin_severity_rank INTEGER
);

-- Raw HIN feature mirror (for reference / debugging).
-- Composite PK because CMAP's segment and intersection layers both number
-- their OBJECTIDs starting at 1 — feature_id alone collides between kinds.
CREATE TABLE IF NOT EXISTS hin_features (
    feature_id TEXT NOT NULL,
    kind TEXT NOT NULL,           -- "segment" | "intersection"
    modal_bike INTEGER NOT NULL DEFAULT 0,
    modal_ped INTEGER NOT NULL DEFAULT 0,
    severity_rank INTEGER,
    source_geom BLOB NOT NULL,    -- original HIN geometry (WKB)
    PRIMARY KEY (feature_id, kind)
);

-- Points of interest. Sourced from brokenspoke or CDP.
CREATE TABLE IF NOT EXISTS pois (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    address TEXT,
    category TEXT NOT NULL,       -- "school" | "park" | "grocery" | "hospital" | "alderman" | "library" | "transit" | ...
    source TEXT NOT NULL,         -- "brokenspoke" | "cdp"
    geom BLOB NOT NULL            -- WKB Point (EPSG:4326)
);

CREATE INDEX IF NOT EXISTS idx_pois_category ON pois(category);

-- Treatment library content (loaded from treatments/*.md).
CREATE TABLE IF NOT EXISTS treatments (
    slug TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    ward TEXT,
    location_lat REAL,
    location_lng REAL,
    photo_path TEXT,
    source_url TEXT,
    summary TEXT,
    body_md TEXT NOT NULL
);
