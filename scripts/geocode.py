"""
geocode.py
-----------
Fills in lat/lng for every row in `dal_platforms`, `worker_locations`, and
`platform_customer`, using LocationIQ's geocoding API (Nominatim-compatible
response format, free tier: 5,000 requests/day, 2 requests/second).

For platform_customer, the city/country come from enrich_customers.py (the
customer's HQ), so run that first; rows still blank here just stay NULL.

The geocode query for a row is:
    city + region present       ->  "{city}, {region}, {country}"
    city present (and not ".")  ->  "{city}, {country}"
    city blank or "."           ->  "{country}"

The region (state/province/admin area) is what disambiguates duplicate city
names -- "Villepinte, France" matches two different communes, so city+country
alone leaves the geocoder guessing. Only platform_customer carries a region
today (filled by enrich_customers.py); tables without the column are
unaffected.

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

import math
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


def build_query(city: str, country: str, region: str = ""):
    """
    Returns the geocode query string for a row, or None if there's nothing
    to geocode at all (no city AND no country).

    A city of "" or "." means "country-level only" -- that's not an error,
    it just produces a country query.

    `region` (state/province/admin area) is inserted between city and country
    when present:  "Villepinte, Seine-Saint-Denis, France". This is what keeps
    duplicate city names apart -- France has two Villepintes, and city+country
    alone makes the geocoder guess between them. Rows without a region fall
    back to the old "{city}, {country}" form, so nothing re-geocodes until a
    region is actually filled in.
    """
    city = (city or "").strip()
    country = (country or "").strip()
    region = (region or "").strip()
    if city and city != ".":
        parts = [city]
        if region and region != ".":
            parts.append(region)
        if country:
            parts.append(country)
        return ", ".join(parts)
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


def region_column(cur, table):
    """
    "region" if `table` has that column, else "" -- so a table that doesn't
    carry a region (the two research-sheet tables) keeps working unchanged,
    and adding the column later needs no edit here.
    """
    cols = {r[1] for r in cur.execute(f"PRAGMA table_info({table})")}
    return "region" if "region" in cols else ""


def select_place_columns(cur, table, extra=""):
    """SELECT fragment + whether a region column is being read."""
    has_region = bool(region_column(cur, table))
    cols = (extra + ", " if extra else "") + "city, country"
    if has_region:
        cols += ", region"
    return cols, has_region


def collect_queries(cur):
    """Every distinct place -> query string across all geocoded tables."""
    queries = set()
    for table in GEOCODED_TABLES:
        cols, has_region = select_place_columns(cur, table)
        for row in cur.execute(f"SELECT {cols} FROM {table}").fetchall():
            city, country = row[0], row[1]
            region = row[2] if has_region else ""
            q = build_query(city, country, region)
            if q is not None:
                queries.add(q)
    return queries


def fill_table(cur, table):
    """
    Write lat/lng onto every row in `table` from the cache, by query string.

    A row whose place can no longer be resolved -- nothing left to query, or
    a query with no cache entry -- has its lat/lng CLEARED rather than left
    alone. Without that, a row that loses its city (an LLM re-lookup that
    declines where it previously answered) keeps the coordinates of the place
    it used to name, and plots at a location its own city/country no longer
    support.
    """
    cols, has_region = select_place_columns(cur, table, extra="id")
    cleared = 0
    for row in cur.execute(f"SELECT {cols} FROM {table}").fetchall():
        row_id, city, country = row[0], row[1], row[2]
        region = row[3] if has_region else ""
        q = build_query(city, country, region)
        hit = (
            cur.execute(
                "SELECT lat, lng FROM geocode_cache WHERE query = ?", (q,)
            ).fetchone()
            if q is not None
            else None
        )
        if hit:
            cur.execute(
                f"UPDATE {table} SET lat = ?, lng = ? WHERE id = ?",
                (hit[0], hit[1], row_id),
            )
        else:
            cur.execute(
                f"UPDATE {table} SET lat = NULL, lng = NULL WHERE id = ? "
                f"AND (lat IS NOT NULL OR lng IS NOT NULL)",
                (row_id,),
            )
            cleared += cur.rowcount
    return cleared


# --- Separating coincident points -------------------------------------------
# Rows that geocode to the same query get byte-identical coordinates, so their
# markers sit exactly on top of each other and the ones underneath can never be
# clicked -- zooming doesn't help, because they are the same point at every
# zoom level. This matters most for rows documented only to country level
# (city blank or "."), which all collapse onto one country centroid.
#
# The fix is to push each member of a coincident group onto a small ring around
# the shared point, so they are distinct coordinates that separate as you zoom
# in. The offsets are deliberately modest: the goal is "clickable and distinct",
# not "visually apart at world zoom". Separating markers at world zoom would
# need offsets of whole degrees, which would put points in the wrong province
# or in the sea -- that is a rendering problem, and belongs in the map (marker
# clustering / spiderfier), not in the coordinates.
#
# The true geocode is never lost: it stays in geocode_cache, keyed on the query
# string, and fill_table() rewrites it onto the rows at the start of every run
# before the spread is re-applied.
# table -> the column that identifies ONE node, or None when every row is its
# own node. Rows in a coincident group that share this value are the same thing
# seen several times and get the SAME offset, so they stay together: a customer
# that buys from five platforms is one company at one address, and scattering it
# into five dots a kilometre apart would invent five locations it doesn't have.
# Rows with DIFFERENT values are different entities and get separate offsets.
SPREAD_TABLES = {
    "worker_locations": None,          # each row is a distinct delivery centre
    "platform_customer": "customer",   # one node per customer, however many
                                       # platforms list it
}

# Ring radius in degrees of latitude. Raise if you want more separation, but
# keep the coarse value well inside your smallest country: 0.15 deg is ~17 km,
# while e.g. Taiwan is only ~140 km wide, so a half-degree ring would land
# points offshore.
CITY_SPREAD_DEG = 0.015    # ~1.7 km  -- stays inside the metro area
COARSE_SPREAD_DEG = 0.15   # ~17 km   -- country-level points are arbitrary anyway


def is_coarse(city):
    """True when a row carries no city, so its point is a country centroid."""
    city = (city or "").strip()
    return not city or city == "."


def spread_overlaps(cur, table, identity_col=None):
    """
    Push coincident rows in `table` onto a small ring around their shared point.

    `identity_col` names the column that identifies one node (see
    SPREAD_TABLES). Rows sharing that value are one entity and all receive the
    same offset; distinct values are placed at distinct points on the ring. A
    point holding only ONE entity is left exactly where it is, however many rows
    sit on it -- there is nothing to separate.

    Deterministic and idempotent: identities are sorted, so the same input
    always produces the same offsets, and because fill_table() resets every row
    to its cached coordinate first, re-running geocode.py recomputes the same
    ring rather than drifting further each time.
    """
    ident = identity_col or "id"
    rows = cur.execute(
        f"SELECT id, lat, lng, city, {ident} FROM {table} "
        f"WHERE lat IS NOT NULL AND lng IS NOT NULL ORDER BY id"
    ).fetchall()

    groups = {}
    for row_id, lat, lng, city, identity in rows:
        groups.setdefault((lat, lng), []).append((row_id, city, identity))

    spread = []
    for (lat, lng), members in sorted(groups.items()):
        # Distinct entities on this point, in a stable order.
        identities = sorted({str(m[2]) for m in members})
        if len(identities) < 2:
            continue
        slot = {name: i for i, name in enumerate(identities)}
        # One country-level row in the group makes the whole point coarse.
        radius = (
            COARSE_SPREAD_DEG
            if any(is_coarse(c) for _, c, _ in members)
            else CITY_SPREAD_DEG
        )
        # A degree of longitude shrinks toward the poles; divide by cos(lat)
        # so the ring stays round on the ground instead of being squashed.
        lat_scale = max(math.cos(math.radians(lat)), 0.01)
        for row_id, _, identity in members:
            angle = 2 * math.pi * slot[str(identity)] / len(identities)
            new_lat = round(lat + radius * math.sin(angle), 7)
            new_lng = round(lng + radius * math.cos(angle) / lat_scale, 7)
            cur.execute(
                f"UPDATE {table} SET lat = ?, lng = ? WHERE id = ?",
                (new_lat, new_lng, row_id),
            )
        spread.append((len(members), len(identities), radius, lat, lng))
    return spread


def main():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    all_queries = collect_queries(cur)
    already_cached = {r[0] for r in cur.execute("SELECT query FROM geocode_cache")}
    to_fetch = sorted(all_queries - already_cached)

    print(
        f"{len(all_queries)} unique place(s) referenced, "
        f"{len(already_cached)} already cached, {len(to_fetch)} to fetch."
    )

    # The token is only needed to FETCH. Writing already-cached coordinates
    # onto the rows is pure local work, so it still runs without a token --
    # otherwise a build_db.py rebuild would strand the database behind an
    # API key even though every coordinate is already cached.
    if to_fetch and not API_KEY:
        print(
            '\nLOCATIONIQ_API_KEY is not set, so the fetches above were skipped. Run:\n'
            '  export LOCATIONIQ_API_KEY="your-token-here"\n'
            "then re-run to fetch them. Joining the cache onto the rows anyway..."
        )
        to_fetch = []

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
    total_cleared = 0
    for table in GEOCODED_TABLES:
        total_cleared += fill_table(cur, table)
    # Separate any rows that landed on exactly the same point.
    for table, identity_col in SPREAD_TABLES.items():
        spread = spread_overlaps(cur, table, identity_col)
        if spread:
            rows_n = sum(n for n, _, _, _, _ in spread)
            nodes_n = sum(d for _, d, _, _, _ in spread)
            print(
                f"\nSpread {rows_n} coincident row(s) in {table} into {nodes_n} "
                f"node(s) across {len(spread)} shared point(s):"
            )
            for n, distinct, radius, lat, lng in spread:
                kind = "country-level" if radius == COARSE_SPREAD_DEG else "city-level"
                extra = f", {n} rows" if n != distinct else ""
                print(
                    f"  {distinct} node(s){extra} at {lat:.5f},{lng:.5f}  "
                    f"({kind}, +/-{radius} deg)"
                )

    conn.commit()
    if total_cleared:
        print(
            f"\nCleared stale coordinates on {total_cleared} row(s) whose place "
            f"no longer resolves (city removed, or query not in the cache)."
        )

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
