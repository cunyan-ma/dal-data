-- ============================================================
-- DAL Research Database — schema
-- ============================================================
-- dal_platforms and worker_locations are FULLY REBUILT every
-- time build_db.py runs, straight from the two raw CSVs:
--   data/raw - dal-platforms.csv
--   data/raw - worker-locations.csv
-- They hold no information you typed directly into the database —
-- the CSVs (which mirror your two research Google Sheets) are the
-- real source of truth for those two tables.
--
-- geocode_cache is the ONLY table that is precious and persistent.
-- It is never dropped or rebuilt — only added to. It holds the
-- one thing that's expensive to redo: API calls to a geocoder,
-- keyed on the exact query string ("City, Country" or "Country").
-- ============================================================

DROP TABLE IF EXISTS dal_platforms;
DROP TABLE IF EXISTS worker_locations;

-- One row per platform. Every platform is a BPO by definition in
-- this design — there is no "is this a BPO?" flag anymore.
CREATE TABLE dal_platforms (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    name     TEXT NOT NULL,
    country  TEXT,
    city     TEXT,
    lat      REAL,   -- filled in by geocode.py, NULL until then
    lng      REAL,   -- filled in by geocode.py, NULL until then
    notes    TEXT
);

-- One row per documented worker location.
-- `platform` is stored as plain text, NOT a SQL foreign key. The
-- ingest script (build_db.py) validates it against dal_platforms.name
-- and logs mismatches loudly — see the validation step there.
CREATE TABLE worker_locations (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    country   TEXT,
    platform  TEXT,   -- validated against dal_platforms.name at ingest
    city      TEXT,
    address   TEXT,   -- raw, full citation string (street-level, legal name, etc.)
    lat       REAL,   -- filled in by geocode.py, NULL until then
    lng       REAL,   -- filled in by geocode.py, NULL until then
    method    TEXT,
    source    TEXT,
    notes     TEXT
);

-- Looked up by the QUERY STRING itself, not by any row or platform.
-- "Nairobi, Kenya" is geocoded once, ever, no matter how many
-- platforms or rows reference it. Survives every rebuild.
CREATE TABLE IF NOT EXISTS geocode_cache (
    query       TEXT PRIMARY KEY,
    lat         REAL,
    lng         REAL,
    fetched_at  TEXT
);
