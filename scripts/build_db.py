"""
build_db.py
------------
Ingests the two research CSVs and (re)builds the `dal_platforms` and
`worker_locations` tables in dal.sqlite.

Input files (downloaded straight from the Google Sheets, by these exact
names — no renaming required):
    data/raw - dal-platforms.csv      columns: name, country, city, notes
    data/raw - worker-locations.csv   columns: country, platform, city,
                                                address, method, source, notes
    data/relationships_data.csv       external platform<->customer links;
                                       columns: source, target,
                                       relationship_type, ...

Safe to re-run any time you update a sheet: dal_platforms, worker_locations,
and platform_customer are fully dropped and rebuilt from the CSVs every run,
so the CSVs (not SQLite) stay the source of truth. geocode_cache and
customer_hq_cache are never touched here -- they're handled by geocode.py /
enrich_customers.py and persist across runs.

platform_customer holds every customer (relationship "target") of a platform
that appears in dal-platforms (relationship "source", relationship_type
"Customer"). Its country/city/lat/lng are left NULL here; run
enrich_customers.py (LLM HQ lookup) then geocode.py to fill them.

This script does NOT geocode or call any LLM. It loads rows with the
derived/looked-up columns left NULL; run enrich_customers.py and geocode.py
afterwards to fill them in.
"""

import csv
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLATFORMS_CSV = ROOT / "data" / "raw - dal-platforms.csv"
WORKERS_CSV = ROOT / "data" / "raw - worker-locations.csv"
RELATIONSHIPS_CSV = ROOT / "data" / "relationships_data.csv"
DB_PATH = ROOT / "dal.sqlite"
SCHEMA_PATH = ROOT / "schema.sql"

# Non-canonical country spellings worth flagging at ingest. The raw data
# has been known to mix e.g. "USA" and "United States" for one country,
# which would silently fragment country-level aggregation on the map.
# We flag (don't auto-rewrite) so a human decides the canonical form.
COUNTRY_ALIASES = {
    "USA": "United States",
    "U.S.A.": "United States",
    "US": "United States",
    "U.S.": "United States",
    "UK": "United Kingdom",
    "U.K.": "United Kingdom",
}


def read_rows(path: Path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def read_customer_links(valid_names):
    """
    From relationships_data.csv, keep every Customer relationship whose
    `source` is one of our platforms. Returns a de-duplicated, sorted list
    of (platform, customer) pairs.

    Rows whose source isn't an exact dal-platforms name are dropped by
    design (e.g. "IngeData" won't match the platform "Ingedata") -- the
    excluded sources are returned too, so the caller can flag near-misses.
    """
    if not RELATIONSHIPS_CSV.exists():
        return [], []

    pairs, excluded = set(), set()
    for r in read_rows(RELATIONSHIPS_CSV):
        if (r.get("relationship_type") or "").strip().lower() != "customer":
            continue
        source = (r.get("source") or "").strip()
        target = (r.get("target") or "").strip()
        if not source or not target:
            continue
        if source in valid_names:
            pairs.add((source, target))
        else:
            excluded.add(source)
    return sorted(pairs), sorted(excluded)


def main():
    platforms = read_rows(PLATFORMS_CSV)
    workers = read_rows(WORKERS_CSV)

    # ---- Validate -------------------------------------------------------
    # 1. Duplicate platform names.
    seen, dup_names = set(), []
    for r in platforms:
        name = (r["name"] or "").strip()
        if name in seen:
            dup_names.append(name)
        seen.add(name)

    valid_names = {(r["name"] or "").strip() for r in platforms}
    customer_links, excluded_sources = read_customer_links(valid_names)

    # 2. worker-locations.platform values with no exact match in dal-platforms.
    #    Logged loudly so a typo can't quietly create an orphan row.
    mismatches = sorted(
        {
            (r["platform"] or "").strip()
            for r in workers
            if (r["platform"] or "").strip() not in valid_names
        }
    )

    # 3. Non-canonical country spellings (flag only).
    bad_countries = sorted(
        {
            c
            for r in (platforms + workers)
            for c in [(r.get("country") or "").strip()]
            if c in COUNTRY_ALIASES
        }
    )

    # ---- Rebuild tables -------------------------------------------------
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    cur = conn.cursor()

    for r in platforms:
        cur.execute(
            "INSERT INTO dal_platforms (name, country, city, notes) "
            "VALUES (?, ?, ?, ?)",
            (
                (r["name"] or "").strip(),
                (r.get("country") or "").strip(),
                (r.get("city") or "").strip(),
                (r.get("notes") or "").strip(),
            ),
        )

    for r in workers:
        cur.execute(
            "INSERT INTO worker_locations "
            "(country, platform, city, address, method, source, notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                (r.get("country") or "").strip(),
                (r["platform"] or "").strip(),
                (r.get("city") or "").strip(),
                (r.get("address") or "").strip(),
                (r.get("method") or "").strip(),
                (r.get("source") or "").strip(),
                (r.get("notes") or "").strip(),
            ),
        )

    for platform, customer in customer_links:
        cur.execute(
            "INSERT INTO platform_customer (platform, customer) VALUES (?, ?)",
            (platform, customer),
        )

    conn.commit()
    conn.close()

    # ---- Report ---------------------------------------------------------
    print(
        f"Loaded {len(platforms)} platform(s), {len(workers)} "
        f"worker-location row(s), and {len(customer_links)} platform-customer "
        f"link(s)."
    )

    if dup_names:
        print(f"\n⚠  Duplicate platform name(s) in dal-platforms:")
        for n in sorted(set(dup_names)):
            print(f"  - {n!r}")

    if mismatches:
        print(
            f"\n⚠  {len(mismatches)} worker-locations platform(s) do NOT exactly "
            f"match any dal-platforms name:"
        )
        for m in mismatches:
            print(f"  - {m!r}")
        print(
            "  Fix the spelling in one of the sheets so they match exactly "
            "(e.g. 'TaskUs, Inc.' -> 'TaskUs', 'Impact Enterprises' -> "
            "'Impact Enterprise'), re-export, and re-run."
        )

    if bad_countries:
        print(f"\n⚠  Non-canonical country spelling(s) found:")
        for c in bad_countries:
            print(f"  - {c!r}  (did you mean {COUNTRY_ALIASES[c]!r}?)")

    if excluded_sources:
        print(
            f"\nℹ  {len(excluded_sources)} relationship source(s) are NOT dal-platforms "
            f"and were skipped (their customers are not tracked):"
        )
        preview = excluded_sources[:8]
        for s in preview:
            print(f"  - {s!r}")
        if len(excluded_sources) > len(preview):
            print(f"  ... and {len(excluded_sources) - len(preview)} more")
        print(
            "  If one of these should be tracked, check for a spelling mismatch "
            "(e.g. 'IngeData' vs the platform 'Ingedata')."
        )

    if not (dup_names or mismatches or bad_countries):
        print("\nNo validation problems found.")

    print(
        "\nNext: run scripts/enrich_customers.py (LLM HQ lookup for customers), "
        "then scripts/geocode.py to fill in lat/lng."
    )


if __name__ == "__main__":
    main()
