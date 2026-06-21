"""
build_db.py
------------
Rebuilds the `companies` and `locations` tables from data/workers.csv
(a CSV export of your "workers" Google Sheet tab).

Safe to re-run any time you update the sheet: companies and locations
are fully dropped and rebuilt from the CSV every run. geocode_cache is
never touched here -- it's handled by geocode.py and persists across runs.
"""

import csv
import re
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = ROOT / "data" / "workers.csv"
DB_PATH = ROOT / "dal.sqlite"
SCHEMA_PATH = ROOT / "schema.sql"

# Legal-entity tokens that show up in "specific location" when no city was
# actually documented for that row (just a subsidiary name). If we see one
# of these AND there's no clean city in parentheses, we flag the row rather
# than guess.
LEGAL_SUFFIX_PATTERN = re.compile(
    r"\b(Inc\.?|LLC|L\.L\.C\.|Ltd\.?|GmbH|S\.A\.S?\.?|d\.o\.o\.|Pty|Corp\.?|"
    r"Co\.|S\.L\.|Sociedade|Soci[eé]t[eé]|Anonyme|Impact Sourcing)\b",
    re.IGNORECASE,
)
TRAILING_PAREN_PATTERN = re.compile(r"\(([^()]+)\)\s*$")


def derive_geocode_query(raw_location: str, country: str):
    """
    Returns (geocode_query, needs_review).

    geocode_query is None when there's nothing specific enough to geocode
    (e.g. "." meaning "country-level only" -- that's fine, not an error).
    needs_review is True when the string is ambiguous enough that auto-
    cleaning would be a guess rather than a fact.
    """
    raw = (raw_location or "").strip()

    if raw in ("", "."):
        return None, False

    # Your own convention: a clean city name in parens at the end of a
    # messy address or subsidiary name, e.g. "TaskUs Colombia SAS (Cali)".
    match = TRAILING_PAREN_PATTERN.search(raw)
    if match:
        clean_city = match.group(1).strip()
        return f"{clean_city}, {country}", False

    # No parenthetical clean name. If this looks like a full street
    # address (has digits) or a legal-entity name with no city given,
    # don't guess -- flag it for a human to add a "(City)" hint.
    if re.search(r"\d", raw) or "\n" in raw or LEGAL_SUFFIX_PATTERN.search(raw):
        return None, True

    # Otherwise it's probably already a clean, short place name.
    return f"{raw}, {country}", False


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    cur = conn.cursor()

    company_ids = {}
    flagged_rows = []
    row_count = 0

    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            row_count += 1
            name = (row["company"] or "").strip()
            hq_address = (row.get("company hq") or "").strip() or None
            country = (row["country"] or "").strip()
            raw_location = (row["specific location"] or "").strip()

            if name not in company_ids:
                cur.execute(
                    "INSERT INTO companies (name, hq_address) VALUES (?, ?)",
                    (name, hq_address),
                )
                company_ids[name] = cur.lastrowid
            elif hq_address:
                cur.execute(
                    "UPDATE companies SET hq_address = COALESCE(hq_address, ?) "
                    "WHERE company_id = ?",
                    (hq_address, company_ids[name]),
                )

            geocode_query, needs_review = derive_geocode_query(raw_location, country)
            if needs_review:
                flagged_rows.append((country, name, raw_location))

            cur.execute(
                """
                INSERT INTO locations
                    (company_id, country, raw_location, geocode_query,
                     method, source_url, notes, needs_review)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    company_ids[name],
                    country,
                    raw_location,
                    geocode_query,
                    (row.get("method") or "").strip(),
                    (row.get("source") or "").strip(),
                    (row.get("notes") or "").strip(),
                    int(needs_review),
                ),
            )

    conn.commit()

    print(f"Loaded {row_count} location rows across {len(company_ids)} companies.")
    if flagged_rows:
        print(f"\n{len(flagged_rows)} row(s) need a manual review before they can be geocoded:")
        for country, company, raw in flagged_rows:
            print(f"  - [{country}] {company}: {raw!r}")
        print(
            "\nFix: in the workers sheet, add the clean city in parentheses at the "
            "end of these, e.g. 'Ridiculously Good Outsourcing, Inc. (Toronto)', "
            "matching the convention you're already using elsewhere. Re-export "
            "the CSV and re-run this script."
        )
    else:
        print("No rows flagged for review.")

    conn.close()


if __name__ == "__main__":
    main()
