"""
geocode.py
-----------
Geocodes every distinct geocode_query in `locations` that isn't already
in `geocode_cache`, using OpenStreetMap's Nominatim API.

Run this on your own machine (it needs real internet access).
Safe to re-run any time -- it only fetches what's missing.

Nominatim's usage policy requires a real User-Agent identifying your
app/contact, and a max of 1 request/second. Update CONTACT below
before running.
"""

import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "dal.sqlite"

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
CONTACT = "your-email@example.com"  # <-- replace with a real contact, per Nominatim's policy
HEADERS = {"User-Agent": f"dal-research-map/1.0 ({CONTACT})"}
RATE_LIMIT_SECONDS = 1.1  # stay safely under Nominatim's 1 req/sec limit


def geocode(query: str):
    resp = requests.get(
        NOMINATIM_URL,
        params={"q": query, "format": "json", "limit": 1},
        headers=HEADERS,
        timeout=10,
    )
    resp.raise_for_status()
    results = resp.json()
    if not results:
        return None
    return float(results[0]["lat"]), float(results[0]["lon"])


def main():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("SELECT DISTINCT geocode_query FROM locations WHERE geocode_query IS NOT NULL")
    all_queries = [r[0] for r in cur.fetchall()]

    cur.execute("SELECT query_key FROM geocode_cache")
    already_cached = {r[0] for r in cur.fetchall()}

    to_fetch = [q for q in all_queries if q not in already_cached]
    print(
        f"{len(all_queries)} unique place(s) referenced, "
        f"{len(already_cached)} already cached, {len(to_fetch)} to fetch."
    )

    not_found = []
    for i, query in enumerate(to_fetch, 1):
        print(f"[{i}/{len(to_fetch)}] {query}")
        result = geocode(query)
        if result is None:
            not_found.append(query)
            print("    no match")
        else:
            lat, lon = result
            cur.execute(
                "INSERT INTO geocode_cache (query_key, lat, long, geocoder, fetched_at) "
                "VALUES (?, ?, ?, 'nominatim', ?)",
                (query, lat, lon, datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
            print(f"    {lat}, {lon}")
        time.sleep(RATE_LIMIT_SECONDS)

    if not_found:
        print(f"\n{len(not_found)} place(s) had no match -- add these manually, e.g.:")
        for q in not_found:
            print(f"  sqlite3 dal.sqlite \"INSERT INTO geocode_cache VALUES "
                  f"('{q}', <lat>, <long>, 'manual', '{datetime.now(timezone.utc).isoformat()}');\"")

    conn.close()


if __name__ == "__main__":
    main()
