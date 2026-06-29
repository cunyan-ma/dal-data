-- ============================================================
-- DAL Research Database — schema
-- ============================================================
-- dal_platforms, worker_locations, and platform_customer are FULLY
-- REBUILT every time build_db.py runs, straight from the raw CSVs:
--   data/raw - dal-platforms.csv
--   data/raw - worker-locations.csv
--   data/relationships_data.csv   (external platform<->customer links)
-- They hold no information you typed directly into the database —
-- the CSVs are the real source of truth for those three tables.
--
-- geocode_cache and customer_hq_cache are the ONLY precious, persistent
-- tables. They are never dropped or rebuilt — only added to. They hold
-- the two things that are expensive to redo:
--   * geocode_cache      — geocoder API calls, keyed on the query string
--   * customer_hq_cache  — LLM HQ lookups, keyed on the customer name
-- ============================================================

DROP TABLE IF EXISTS dal_platforms;
DROP TABLE IF EXISTS worker_locations;
DROP TABLE IF EXISTS platform_customer;

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

-- One row per (platform, customer) link, built from the external
-- relationships_data.csv: every customer (target) of a platform that
-- appears in dal_platforms. country/city/lat/lng describe the CUSTOMER's
-- HQ -- country/city are filled by enrich_customers.py (LLM lookup),
-- lat/lng by geocode.py. Long/flat structure: one customer per row.
CREATE TABLE platform_customer (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    platform  TEXT NOT NULL,   -- a dal_platforms.name (the relationship "source")
    customer  TEXT NOT NULL,   -- the relationship "target"
    country   TEXT,            -- customer HQ country, filled by enrich_customers.py
    city      TEXT,            -- customer HQ city, filled by enrich_customers.py
    lat       REAL,            -- filled in by geocode.py, NULL until then
    lng       REAL             -- filled in by geocode.py, NULL until then
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

-- Looked up by the CUSTOMER NAME itself. Each customer's HQ is resolved
-- once, ever, by an LLM call (enrich_customers.py), no matter how many
-- platforms list it as a customer. Survives every rebuild, exactly like
-- geocode_cache -- it holds the result of expensive API calls.
CREATE TABLE IF NOT EXISTS customer_hq_cache (
    customer    TEXT PRIMARY KEY,
    country     TEXT,
    city        TEXT,
    fetched_at  TEXT
);
