-- ============================================================
-- DAL Research Database — schema
-- ============================================================
-- companies and locations are FULLY REBUILT every time
-- build_db.py runs, straight from data/workers.csv.
-- They hold no information you typed directly into the database —
-- the CSV (which mirrors your "workers" Google Sheet) is the
-- real source of truth for those two tables.
--
-- geocode_cache is the ONLY table that is precious and persistent.
-- It is never dropped or rebuilt — only added to. It holds the
-- one thing that's expensive to redo: API calls to a geocoder.
-- ============================================================

DROP TABLE IF EXISTS locations;
DROP TABLE IF EXISTS companies;

-- One row per company, ever. No company name or HQ address is
-- ever duplicated across rows — every location just points here.
CREATE TABLE companies (
    company_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    hq_address  TEXT,
    hq_lat      REAL,
    hq_long     REAL
);

-- One row per documented worker location — same granularity as
-- your "workers" sheet today. company_id is a POINTER (a foreign
-- key) to a row in companies, not a copy of the company's data.
CREATE TABLE locations (
    location_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id      INTEGER NOT NULL REFERENCES companies(company_id),
    country         TEXT NOT NULL,
    raw_location    TEXT NOT NULL,   -- the literal string from your sheet (for citation)
    geocode_query   TEXT,            -- cleaned place name actually sent to the geocoder
    method          TEXT,
    source_url      TEXT,
    notes           TEXT,
    needs_review    INTEGER NOT NULL DEFAULT 0  -- 1 = couldn't safely auto-clean this address
);

-- Looked up by the QUERY TEXT itself, not by any row or company.
-- "Nairobi, Kenya" is geocoded once, ever, no matter how many
-- companies or rows reference it.
CREATE TABLE IF NOT EXISTS geocode_cache (
    query_key   TEXT PRIMARY KEY,
    lat         REAL,
    long        REAL,
    geocoder    TEXT,
    fetched_at  TEXT
);
