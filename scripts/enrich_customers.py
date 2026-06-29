"""
enrich_customers.py
--------------------
Fills in the HQ `country` and `city` for every customer in the
`platform_customer` table, using an LLM (Claude) lookup.

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
"""

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import anthropic
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "dal.sqlite"

MODEL = "claude-opus-4-8"

SYSTEM = (
    "You are a precise corporate-research assistant. Given a company name, "
    "return the city and country of that company's global headquarters. "
    "Use the single primary/global HQ. If you are not reasonably confident "
    "of the company or its HQ, return empty strings rather than guessing -- "
    "a wrong city is worse than a blank one. Return the city without any "
    "state/province qualifier (e.g. 'San Francisco', not 'San Francisco, CA'), "
    "and the country as its common English name (e.g. 'United States')."
)


class HQLocation(BaseModel):
    country: str
    city: str


def lookup_hq(client, customer):
    """Returns (country, city). Either/both may be '' if unknown."""
    resp = client.messages.parse(
        model=MODEL,
        max_tokens=512,
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
    return (hq.country or "").strip(), (hq.city or "").strip()


def fill_platform_customer(cur):
    """Write country/city onto every platform_customer row from the cache."""
    cur.execute("SELECT id, customer FROM platform_customer")
    for row_id, customer in cur.fetchall():
        hit = cur.execute(
            "SELECT country, city FROM customer_hq_cache WHERE customer = ?",
            (customer,),
        ).fetchone()
        if hit:
            cur.execute(
                "UPDATE platform_customer SET country = ?, city = ? WHERE id = ?",
                (hit[0], hit[1], row_id),
            )


def main():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print('ANTHROPIC_API_KEY is not set. Run:\n  export ANTHROPIC_API_KEY="sk-ant-..."\nthen try again.')
        return

    client = anthropic.Anthropic()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    customers = sorted({r[0] for r in cur.execute("SELECT customer FROM platform_customer")})
    cached = {r[0] for r in cur.execute("SELECT customer FROM customer_hq_cache")}
    to_fetch = [c for c in customers if c not in cached]

    print(
        f"{len(customers)} distinct customer(s) referenced, "
        f"{len(cached)} already cached, {len(to_fetch)} to look up."
    )

    blanks = []
    for i, customer in enumerate(to_fetch, 1):
        print(f"[{i}/{len(to_fetch)}] {customer}")
        try:
            country, city = lookup_hq(client, customer)
        except anthropic.APIError as e:
            print(f"    LLM error: {e}  (skipping; will retry next run)")
            continue

        cur.execute(
            "INSERT INTO customer_hq_cache (customer, country, city, fetched_at) "
            "VALUES (?, ?, ?, ?)",
            (customer, country, city, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()

        if country or city:
            print(f"    {city or '?'}, {country or '?'}")
        else:
            print("    (HQ unknown — left blank)")
            blanks.append(customer)

    # Join the (now-warm) cache back onto every platform_customer row.
    fill_platform_customer(cur)
    conn.commit()

    if blanks:
        print(f"\n{len(blanks)} customer(s) had no confident HQ and were left blank:")
        for c in blanks:
            print(f"  - {c}")
        print(
            "Fix by hand if you know the HQ, e.g.:\n"
            "  sqlite3 dal.sqlite \"UPDATE customer_hq_cache SET country='...', city='...' "
            "WHERE customer='<name>';\"\n"
            "then re-run this script to join it onto the rows."
        )

    print("\nNext: run scripts/geocode.py to fill in lat/lng.")
    conn.close()


if __name__ == "__main__":
    main()
