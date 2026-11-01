"""
enrich_customers.py
--------------------
Fills in the HQ `country`, `city`, and `region` for every customer in the
`platform_customer` table, using an LLM (Claude) lookup.

`region` is the state/province/department containing the HQ city. It exists
to disambiguate duplicate city names for the geocoder -- "Villepinte, France"
matches two communes, and city+country alone let the geocoder pick the wrong
one. It is not meant for display.

The result of each lookup is cached in `customer_hq_cache`, keyed on the
customer name -- so re-running after adding a few new customers costs a few
LLM calls, not one per row. customer_hq_cache survives the table rebuilds
done by build_db.py, exactly like geocode_cache.

This script does NOT geocode. After it fills country/city, run geocode.py
to turn "{city}, {country}" into lat/lng (the same geocode pipeline the
platforms and worker locations use).

Setup (one-time):
  1. pip install -r requirements.txt
  2. export ANTHROPIC_API_KEY="sk-ant-..."

Run order:  build_db.py  ->  enrich_customers.py  ->  geocode.py  ->  export.py
Safe to re-run any time; it only looks up customers not already cached.

  python3 scripts/enrich_customers.py                     # new customers only
  python3 scripts/enrich_customers.py --backfill-regions   # re-ask for customers
                                                           # cached before `region`
                                                           # existed (costs one LLM
                                                           # call each)

Without an ANTHROPIC_API_KEY the lookups are skipped but the existing cache is
still joined onto the rows, so a rebuilt database can be refilled offline.
"""

import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import anthropic
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "dal.sqlite"

MODEL = "claude-opus-5-5"

# Thinking is always on for this model and shares the max_tokens budget with
# the visible answer, so this can't be sized to the JSON payload alone --
# too small and the response is truncated before the object is emitted.
MAX_TOKENS = 8192

# The HQ lookup is factual recall, not a reasoning problem. "medium" is this
# model's default; it's set explicitly so the choice is visible and tunable.
EFFORT = "medium"

SYSTEM = (
    "You are a precise corporate-research assistant. Given a company name, "
    "return the city, region, and country of that company's global headquarters. "
    "Use the single primary/global HQ. If you are not reasonably confident "
    "of the company or its HQ, return empty strings rather than guessing -- "
    "a wrong city is worse than a blank one. Return the city without any "
    "state/province qualifier (e.g. 'San Francisco', not 'San Francisco, CA'), "
    "and the country as its common English name (e.g. 'United States'). "
    "Return the first-level administrative area containing that city as "
    "`region` -- the state, province, department, prefecture or equivalent "
    "(e.g. 'California' for Foster City, 'Seine-Saint-Denis' for Villepinte, "
    "'Oise' for Senlis). The region is used to tell same-named places apart "
    "when looking up coordinates, so give the administrative area rather than "
    "a metro-area nickname ('Bay Area', 'Greater London'). If you are confident "
    "of the city but not the region, return the city and leave `region` empty."
)


class HQLocation(BaseModel):
    country: str
    city: str
    region: str


def lookup_hq(client, customer):
    """Returns (country, city, region). Any of them may be '' if unknown."""
    resp = client.messages.parse(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        output_config={"effort": EFFORT},
        system=SYSTEM,
        messages=[
            {
                "role": "user",
                "content": f"Where is the global headquarters of the company \"{customer}\"?",
            }
        ],
        output_format=HQLocation,
    )
    hq = resp.parsed_output
    if hq is None:
        # The model stopped without producing the object -- a safety refusal
        # or a truncated response. Treat it like "unknown" so the run carries
        # on; the customer is reported as blank and can be filled by hand.
        print(f"    no structured answer (stop_reason={resp.stop_reason!r})")
        return "", "", ""
    return (
        (hq.country or "").strip(),
        (hq.city or "").strip(),
        (hq.region or "").strip(),
    )


def fill_platform_customer(cur):
    """Write country/city/region onto every platform_customer row from the cache."""
    filled = 0
    cur.execute("SELECT id, customer FROM platform_customer")
    for row_id, customer in cur.fetchall():
        hit = cur.execute(
            "SELECT country, city, region FROM customer_hq_cache WHERE customer = ?",
            (customer,),
        ).fetchone()
        if hit:
            cur.execute(
                "UPDATE platform_customer SET country = ?, city = ?, region = ? "
                "WHERE id = ?",
                (hit[0], hit[1], hit[2], row_id),
            )
            filled += 1
    return filled


def main():
    backfill = "--backfill-regions" in sys.argv

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    customers = sorted({r[0] for r in cur.execute("SELECT customer FROM platform_customer")})
    cached = {r[0] for r in cur.execute("SELECT customer FROM customer_hq_cache")}

    if backfill:
        # Customers cached before `region` existed: re-ask only for those.
        # A region is only useful alongside a city, so country-only and
        # fully-blank entries are left alone.
        to_fetch = [
            r[0]
            for r in cur.execute(
                "SELECT customer FROM customer_hq_cache "
                "WHERE (region IS NULL OR TRIM(region) = '') "
                "  AND city IS NOT NULL AND TRIM(city) <> '' "
                "ORDER BY customer"
            )
        ]
        print(f"Backfill mode: {len(to_fetch)} cached customer(s) have a city but no region.")
    else:
        to_fetch = [c for c in customers if c not in cached]
        print(
            f"{len(customers)} distinct customer(s) referenced, "
            f"{len(cached)} already cached, {len(to_fetch)} to look up."
        )

    # The key is only needed to FETCH. Joining the existing cache onto the
    # rebuilt rows is pure local work, so it still runs without a key --
    # otherwise a build_db.py rebuild would strand the database behind an
    # API key even though every answer is already cached.
    if to_fetch and not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            '\nANTHROPIC_API_KEY is not set, so the lookups above were skipped. Run:\n'
            '  export ANTHROPIC_API_KEY="sk-ant-..."\n'
            "then re-run to fetch them. Joining the cache onto the rows anyway..."
        )
        to_fetch = []

    client = anthropic.Anthropic() if to_fetch else None

    blanks = []
    for i, customer in enumerate(to_fetch, 1):
        print(f"[{i}/{len(to_fetch)}] {customer}")
        try:
            country, city, region = lookup_hq(client, customer)
        except anthropic.APIError as e:
            print(f"    LLM error: {e}  (skipping; will retry next run)")
            continue

        now = datetime.now(timezone.utc).isoformat()
        if backfill:
            cur.execute(
                "UPDATE customer_hq_cache SET country = ?, city = ?, region = ?, "
                "fetched_at = ? WHERE customer = ?",
                (country, city, region, now, customer),
            )
        else:
            cur.execute(
                "INSERT INTO customer_hq_cache "
                "(customer, country, city, region, fetched_at) VALUES (?, ?, ?, ?, ?)",
                (customer, country, city, region, now),
            )
        conn.commit()

        if country or city:
            print(f"    {city or '?'}, {region or '-'}, {country or '?'}")
        else:
            print("    (HQ unknown — left blank)")
            blanks.append(customer)

    # Join the (now-warm) cache back onto every platform_customer row.
    filled = fill_platform_customer(cur)
    conn.commit()
    total_rows = cur.execute("SELECT COUNT(*) FROM platform_customer").fetchone()[0]
    print(f"\nJoined cached HQ onto {filled}/{total_rows} platform_customer row(s).")

    if blanks:
        print(f"\n{len(blanks)} customer(s) had no confident HQ and were left blank:")
        for c in blanks:
            print(f"  - {c}")
        print(
            "Fix by hand if you know the HQ, e.g.:\n"
            "  sqlite3 dal.sqlite \"UPDATE customer_hq_cache SET country='...', "
            "city='...', region='...' WHERE customer='<name>';\"\n"
            "then re-run this script to join it onto the rows."
        )

    print("\nNext: run scripts/geocode.py to fill in lat/lng.")
    conn.close()


if __name__ == "__main__":
    main()
