"""
export_summary.py
-------------------
Joins companies + locations + geocode_cache into one flat table and
writes it to summary.csv -- this is the file your React app actually
reads. Safe to re-run any time; it always reflects the current state
of dal.sqlite.
"""

import csv
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "dal.sqlite"
OUT_PATH = ROOT / "summary.csv"

QUERY = """
SELECT
    l.country,
    c.name          AS company,
    l.raw_location,
    g.lat,
    g.long,
    c.hq_address,
    c.hq_lat,
    c.hq_long,
    l.method,
    l.source_url,
    l.notes,
    l.needs_review
FROM locations l
JOIN companies c
    ON l.company_id = c.company_id
LEFT JOIN geocode_cache g
    ON l.geocode_query = g.query_key
ORDER BY l.country, c.name;
"""


def main():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(QUERY)
    columns = [d[0] for d in cur.description]
    rows = cur.fetchall()

    with open(OUT_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(columns)
        writer.writerows(rows)

    geocoded = sum(1 for r in rows if r[3] is not None)
    print(f"Wrote {len(rows)} rows to {OUT_PATH.name}")
    print(f"  {geocoded} already have coordinates")
    print(f"  {len(rows) - geocoded} are waiting on geocode.py")

    conn.close()


if __name__ == "__main__":
    main()
