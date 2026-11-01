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

The customer's HQ `city`/`country`/`region` are **not** in the file — they're
filled in by `enrich_customers.py`, which asks an LLM (Claude) for each
distinct customer's HQ and caches the answer in `customer_hq_cache`. Those
then feed the same geocoder as everything else.

**`region`** is the state/province/department containing the HQ city
(`California`, `Seine-Saint-Denis`, `Oise`). It exists for one reason: to tell
same-named places apart when geocoding. `"Villepinte, France"` matches two
different communes, and city+country alone let the geocoder pick whichever it
ranks higher — which is how Guerbet's HQ near Paris ended up plotted in
Aude, 790 km away. With a region the query becomes
`"Villepinte, Seine-Saint-Denis, France"` and resolves unambiguously.

`region` is a geocoding aid, not a display field. It is exported in
`platform-customer.csv` so the resolution is auditable, but the map shows
`city`. The two research-sheet tables (`dal_platforms`, `worker_locations`)
have no region column; their queries are unchanged.

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
   Asks Claude for each new customer's HQ city/region/country and caches it
   in `customer_hq_cache`. Only looks up customers not already cached. Leaves
   a customer blank (and prints it) when it isn't confident, so you can
   fill it in by hand.

   Customers cached before `region` existed keep an empty region (and so keep
   their old city+country geocode query). To re-ask for just those:
   ```bash
   python3 scripts/enrich_customers.py --backfill-regions
   ```
   That costs one LLM call per customer, and the regions it fills change those
   rows' geocode query strings — so the next `geocode.py` run will fetch fresh
   coordinates for them. That re-fetch is the point: it's what corrects
   wrong-namesake coordinates.

   Without `ANTHROPIC_API_KEY` the lookups are skipped, but the existing cache
   is still joined onto the rows — so a rebuilt database can be refilled with
   no key and no network.
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
  is looked up by the LLM once, ever. `region` was added to this table after
  it was first populated; `build_db.py` ALTERs it in on an existing database
  (schema.sql's `CREATE TABLE IF NOT EXISTS` can't), leaving the cached rows
  intact with an empty region.

Note: if you edit a row's `city` **or `region`**, its query string changes, so
it'll miss the geocode cache and trigger one fresh LocationIQ call on the next
run. That's expected, not a bug — it's the mechanism that lets a corrected
place name repair its own coordinates.

Neither key check blocks the join step: `enrich_customers.py` and `geocode.py`
both write their cached values onto the rebuilt rows even with no API key set,
since that part is pure local work.

## Rows that land on the same point

Two rows with the same city and country geocode to the same query, so they get
byte-identical coordinates and their markers sit exactly on top of each other --
the one underneath can never be clicked, and zooming doesn't help, because they
are the same point at every zoom level. Rows documented only to country level
(`city` blank or `.`) are the worst case: every platform in that country
collapses onto one country centroid.

After joining the cache onto the rows, `geocode.py` pushes each member of a
coincident group onto a small ring around the shared point, so they become
distinct coordinates that separate as you zoom in:

```
Spread 15 coincident row(s) in worker_locations across 7 shared point(s):
  2 rows at -1.28900,36.81728  (city-level, +/-0.015 deg)
  2 rows at 23.97394,120.98202  (country-level, +/-0.15 deg)
```

Two radii, set at the top of `geocode.py`:

| | radius | ~distance | why |
|---|---|---|---|
| `CITY_SPREAD_DEG` | 0.015 deg | ~1.7 km | keeps the point inside the metro area |
| `COARSE_SPREAD_DEG` | 0.15 deg | ~17 km | a country centroid is arbitrary anyway |

A group counts as country-level if *any* member has no city. Longitude offsets
are divided by `cos(lat)` so the ring stays round on the ground rather than
being squashed toward the poles.

The offsets are deliberately modest. The goal is *clickable and distinct*, not
*visually apart at world zoom* -- separating markers at world zoom would need
offsets of whole degrees, which would put points in the wrong province or in
the sea. If you want visual separation when zoomed out, that belongs in the map
(marker clustering or a spiderfier), not in the coordinates. Raising
`COARSE_SPREAD_DEG` is safe only while it stays well inside your smallest
country: 0.15 deg is ~17 km, and Taiwan is only ~140 km wide.

**The true coordinate is never lost.** It stays in `geocode_cache`, keyed on the
query string, and `fill_table()` rewrites it onto the rows at the start of every
run before the spread is re-applied -- so the offsets are recomputed from the
true point each time, not stacked on top of the previous run's. Re-running
`geocode.py` any number of times gives identical coordinates.

Only `worker_locations` is spread today (`SPREAD_TABLES` in `geocode.py`).
`platform_customer` has far more coincident rows -- 15 customers share one San
Francisco point -- so add it to that tuple if those nodes need separating too.

## If a place can't be geocoded automatically

`geocode.py` prints a ready-to-paste `sqlite3` command to insert the
coordinates by hand once you've looked them up — done once per unique
place, then re-run `geocode.py` to join the new coordinates onto the rows.
