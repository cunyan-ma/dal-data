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

Safe to re-run any time you update either sheet: both tables are fully
dropped and rebuilt from the CSVs every run, so the CSVs (not SQLite)
stay the source of truth. geocode_cache is never touched here -- it's
handled by geocode.py and persists across runs.

This script does NOT geocode. It loads rows with lat/lng left NULL;
run geocode.py afterwards to fill them in.
"""

import csv
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLATFORMS_CSV = ROOT / "data" / "raw - dal-platforms.csv"
WORKERS_CSV = ROOT / "data" / "raw - worker-locations.csv"
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

    conn.commit()
    conn.close()

    # ---- Report ---------------------------------------------------------
    print(
        f"Loaded {len(platforms)} platform(s) and {len(workers)} "
        f"worker-location row(s)."
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

    if not (dup_names or mismatches or bad_countries):
        print("No validation problems found.")

    print("\nNext: run scripts/geocode.py to fill in lat/lng.")


if __name__ == "__main__":
    main()
