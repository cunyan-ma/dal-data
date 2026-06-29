"""
geocode.py
-----------
Fills in lat/lng for every row in `dal_platforms`, `worker_locations`, and
`platform_customer`, using LocationIQ's geocoding API (Nominatim-compatible
response format, free tier: 5,000 requests/day, 2 requests/second).

For platform_customer, the city/country come from enrich_customers.py (the
customer's HQ), so run that first; rows still blank here just stay NULL.

The geocode query for a row is:
    city present (and not ".")  ->  "{city}, {country}"
    city blank or "."           ->  "{country}"

Every distinct query is looked up at most once, ever: results are stored
in `geocode_cache` (keyed on the exact query string), which survives the
table rebuilds done by build_db.py. After the cache is warm, the lat/lng
is written back onto the rebuilt rows in both tables.

Setup (one-time):
  1. Sign up free at https://locationiq.com and grab an access token.
  2. In your terminal, before running this script:
       export LOCATIONIQ_API_KEY="your-token-here"

Run this on your own machine -- it needs real internet access.
Safe to re-run any time; it only fetches queries not already cached.
"""

import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "dal.sqlite"

LOCATIONIQ_URL = "https://us1.locationiq.com/v1/search"
API_KEY = os.environ.get("LOCATIONIQ_API_KEY", "")
RATE_LIMIT_SECONDS = 0.6  # respects the 2 req/sec cap
MAX_RETRIES = 3
DEFAULT_BACKOFF_SECONDS = 60  # LocationIQ's per-minute limit is a rolling window


def build_query(city: str, country: str):
    """
    Returns the geocode query string for a row, or None if there's nothing
    to geocode at all (no city AND no country).

    A city of "" or "." means "country-level only" -- that's not an error,
    it just produces a country query.
    """
    city = (city or "").strip()
    country = (country or "").strip()
    if city and city != ".":
        return f"{city}, {country}" if country else city
    return country or None


def geocode(query: str):
    """Returns (lat, lng) or None if no match. Raises requests.HTTPError on real errors."""
    resp = requests.get(
        LOCATIONIQ_URL,
        params={"key": API_KEY, "q": query, "format": "json", "limit": 1},
        timeout=10,
    )
    if resp.status_code == 404:
        return None  # LocationIQ's way of saying "no match found" -- not an error
    resp.raise_for_status()
    results = resp.json()
    if not results:
        return None
    return float(results[0]["lat"]), float(results[0]["lon"])


def geocode_with_retry(query: str):
    """
    Same as geocode(), but if LocationIQ returns 429 (rate limited), waits
    and retries instead of giving up immediately. Other HTTP errors are
    logged and treated as a non-retryable failure for this query.
    """
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return geocode(query), None
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            if status == 429:
                wait = int(e.response.headers.get("Retry-After", DEFAULT_BACKOFF_SECONDS))
                print(f"    rate limited (attempt {attempt}/{MAX_RETRIES}), waiting {wait}s...")
                time.sleep(wait)
                continue
            body = e.response.text[:200] if e.response is not None else ""
            return None, f"HTTP error: {e} -- {body}"
    return None, "gave up after repeated rate-limit retries"


GEOCODED_TABLES = ("dal_platforms", "worker_locations", "platform_customer")


def collect_queries(cur):
    """Every distinct (city, country) -> query string across all geocoded tables."""
    queries = set()
    for table in GEOCODED_TABLES:
        cur.execute(f"SELECT city, country FROM {table}")
        for city, country in cur.fetchall():
            q = build_query(city, country)
            if q is not None:
                queries.add(q)
    return queries


def fill_table(cur, table):
    """Write lat/lng onto every row in `table` from the cache, by query string."""
    cur.execute(f"SELECT id, city, country FROM {table}")
    for row_id, city, country in cur.fetchall():
        q = build_query(city, country)
        if q is None:
            continue
        hit = cur.execute(
            "SELECT lat, lng FROM geocode_cache WHERE query = ?", (q,)
        ).fetchone()
        if hit:
            cur.execute(
                f"UPDATE {table} SET lat = ?, lng = ? WHERE id = ?",
                (hit[0], hit[1], row_id),
            )


def main():
    if not API_KEY:
        print('LOCATIONIQ_API_KEY is not set. Run:\n  export LOCATIONIQ_API_KEY="your-token-here"\nthen try again.')
        return

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    all_queries = collect_queries(cur)
    already_cached = {r[0] for r in cur.execute("SELECT query FROM geocode_cache")}
    to_fetch = sorted(all_queries - already_cached)

    print(
        f"{len(all_queries)} unique place(s) referenced, "
        f"{len(already_cached)} already cached, {len(to_fetch)} to fetch."
    )

    not_found = []
    for i, query in enumerate(to_fetch, 1):
        print(f"[{i}/{len(to_fetch)}] {query}")
        result, error = geocode_with_retry(query)

        if error:
            print(f"    {error}")
            not_found.append(query)
        elif result is None:
            print("    no match")
            not_found.append(query)
        else:
            lat, lng = result
            cur.execute(
                "INSERT INTO geocode_cache (query, lat, lng, fetched_at) "
                "VALUES (?, ?, ?, ?)",
                (query, lat, lng, datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
            print(f"    {lat}, {lng}")

        time.sleep(RATE_LIMIT_SECONDS)

    # Join the (now-warm) cache back onto every row in all geocoded tables.
    for table in GEOCODED_TABLES:
        fill_table(cur, table)
    conn.commit()

    if not_found:
        now = datetime.now(timezone.utc).isoformat()
        print(f"\n{len(not_found)} place(s) had no match or failed -- add these manually, e.g.:")
        for q in not_found:
            print(
                f"  sqlite3 dal.sqlite \"INSERT INTO geocode_cache (query, lat, lng, fetched_at) "
                f"VALUES ('{q}', <lat>, <lng>, '{now}');\""
            )
        print("Then re-run this script to join the new coordinates onto the rows.")

    conn.close()


if __name__ == "__main__":
    main()
