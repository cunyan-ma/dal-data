"""
export.py
----------
Writes the two flat CSVs the React/Leaflet map actually reads:

    dal-platforms.csv      <- SELECT name, country, city, lat, lng, notes
    worker-location.csv    <- SELECT country, platform, city, lat, lng,
                                       method, source, notes

No JOIN is needed: `platform` is stored as plain text on worker_locations,
and lat/lng were written onto each table by geocode.py. Safe to re-run any
time; it always reflects the current state of dal.sqlite.

Note: `address` is intentionally dropped from the worker-location export --
it's a citation/QA field for this research repo, not something the public
map needs to display.
"""

import csv
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "dal.sqlite"

EXPORTS = [
    (
        ROOT / "dal-platforms.csv",
        "SELECT name, country, city, lat, lng, notes FROM dal_platforms "
        "ORDER BY country, name",
    ),
    (
        ROOT / "worker-location.csv",
        "SELECT country, platform, city, lat, lng, method, source, notes "
        "FROM worker_locations ORDER BY country, platform",
    ),
]


def main():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    for out_path, query in EXPORTS:
        cur.execute(query)
        columns = [d[0] for d in cur.description]
        rows = cur.fetchall()

        with open(out_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(columns)
            writer.writerows(rows)

        # lat is the 4th selected column in both queries.
        geocoded = sum(1 for r in rows if r[3] is not None)
        print(f"Wrote {len(rows)} rows to {out_path.name}")
        print(f"  {geocoded} have coordinates, {len(rows) - geocoded} waiting on geocode.py")

    conn.close()


if __name__ == "__main__":
    main()
