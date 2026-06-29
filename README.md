# DAL research database

Canonical source of truth for DAL platform/worker-location data, separate
from the DAL Map app repo. Research is done by hand in two Google Sheets
with **no coordinates** — scripts geocode and populate the database
afterward. A third, external dataset (`relationships_data.csv`) links each
platform to its customers. The app consumes three clean CSVs exported at the
end of the pipeline: `dal-platforms.csv`, `worker-location.csv`, and
`platform-customer.csv`.

## Layout

```
dal-data/
├── data/
│   ├── raw - dal-platforms.csv      <- CSV export of the "dal-platforms" sheet
│   ├── raw - worker-locations.csv   <- CSV export of the "worker-locations" sheet
│   └── relationships_data.csv       <- external platform<->customer links
├── schema.sql                <- table definitions, with comments
├── scripts/
│   ├── build_db.py           <- validates + rebuilds the 3 tables from the CSVs
│   ├── enrich_customers.py   <- LLM lookup of each customer's HQ city/country
│   ├── geocode.py            <- fetches lat/lng for new places, caches, fills rows
│   └── export.py             <- writes the three CSVs the app reads
├── dal.sqlite                <- the database itself (generated)
├── dal-platforms.csv         <- app input (generated)
├── worker-location.csv       <- app input (generated)
└── platform-customer.csv     <- app input (generated)
```

## The two research sheets

Human-typed only. No lat/lng, no SQL at entry time.

**`dal-platforms`** — one row per platform (every platform is a BPO by
definition in this design):

| name | country | city | notes |
|---|---|---|---|

**`worker-locations`** — one row per documented worker location:

| country | platform | city | address | method | source | notes |
|---|---|---|---|---|---|---|

- **`platform`** must match a `dal-platforms.name` **exactly**. It's stored
  as plain text, not a SQL foreign key, but `build_db.py` validates it and
  logs mismatches loudly (e.g. `TaskUs, Inc.` vs `TaskUs`).
- **`address`** is the raw, full citation string (street-level detail,
  subsidiary legal name, etc.), kept for citation integrity. It is **not**
  geocoded and is **not** exported to the app — `city` + `country` is what
  gets geocoded.
- **`city`** may be `.` or blank for country-level-only signals; both are
  treated as "no city", producing a country-level geocode query.

## The customer dataset (`relationships_data.csv`)

This is an **external** file (not a research sheet you maintain by hand). It
links companies: `source`, `target`, `relationship_type`, plus source URLs.
`build_db.py` keeps only the rows where `relationship_type` is `Customer`
**and** `source` exactly matches a platform in `dal-platforms` — those become
the `platform_customer` table (one row per platform→customer link). Sources
that aren't platforms (e.g. `Appen`, `Scale AI`) are skipped, and a near-miss
spelling like `IngeData` vs the platform `Ingedata` is reported so you can
fix it.

The customer's HQ `city`/`country` are **not** in the file — they're filled
in by `enrich_customers.py`, which asks an LLM (Claude) for each distinct
customer's HQ and caches the answer in `customer_hq_cache`. Those then feed
the same geocoder as everything else.

## One-time setup

```bash
git init
python3 -m venv .venv
source .venv/bin/activate        # on Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Running the pipeline

Every time you've edited either sheet:

1. **Export both sheets to CSV** and overwrite the files in `data/`. They
   download with the exact names the script expects — no renaming needed:
   - `data/raw - dal-platforms.csv`
   - `data/raw - worker-locations.csv`
2. **Validate + rebuild the database:**
   ```bash
   python3 scripts/build_db.py
   ```
   Fully rebuilds `dal_platforms`, `worker_locations`, and
   `platform_customer` from the CSVs (HQ/lat/lng left empty for now). It
   prints any duplicate platform names, any `worker-locations.platform`
   that doesn't exactly match a platform name, non-canonical country
   spellings (e.g. `USA` vs `United States`), and any relationship sources
   that look like a near-miss for a platform. Fix those in the sheets and
   re-run.
3. **Look up customer HQs (LLM):**
   ```bash
   export ANTHROPIC_API_KEY="sk-ant-..."          # one-time per shell
   python3 scripts/enrich_customers.py
   ```
   Asks Claude for each new customer's HQ city/country and caches it in
   `customer_hq_cache`. Only looks up customers not already cached. Leaves
   a customer blank (and prints it) when it isn't confident, so you can
   fill it in by hand.
4. **Geocode anything new:**
   ```bash
   export LOCATIONIQ_API_KEY="your-token-here"    # one-time per shell
   python3 scripts/geocode.py
   ```
   Only fetches places not already in `geocode_cache`, then writes the
   lat/lng back onto every row in all three tables. Sign up free at
   https://locationiq.com for a token.
5. **Export the CSVs the app reads:**
   ```bash
   python3 scripts/export.py
   ```
   Writes `dal-platforms.csv`, `worker-location.csv`, and
   `platform-customer.csv`. Copy these into the DAL Map app (e.g.
   `dal-map/public/data/`) or push them to wherever the app reads from.

## Why the data tables get rebuilt but the caches don't

`dal_platforms`, `worker_locations`, and `platform_customer` hold nothing
that isn't already in the source CSVs — there's no reason to "merge" or
"update" them carefully, just regenerate them fresh every run and let
SQLite hand out new IDs.

The two cache tables are different: they hold the result of real, expensive
API calls, keyed on a text value that survives a rebuild rather than on any
row or ID — so they're never dropped, only added to.

- `geocode_cache` is keyed on the query string (`"City, Country"` or
  `"Country"`), so each place is geocoded once, ever.
- `customer_hq_cache` is keyed on the customer name, so each customer's HQ
  is looked up by the LLM once, ever.

Note: if you edit a row's `city`, its query string changes, so it'll miss
the geocode cache and trigger one fresh LocationIQ call on the next run.
That's expected, not a bug.

## If a place can't be geocoded automatically

`geocode.py` prints a ready-to-paste `sqlite3` command to insert the
coordinates by hand once you've looked them up — done once per unique
place, then re-run `geocode.py` to join the new coordinates onto the rows.
