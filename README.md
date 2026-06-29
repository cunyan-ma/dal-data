# DAL research database

Canonical source of truth for DAL platform/worker-location data, separate
from the DAL Map app repo. Research is done by hand in two Google Sheets
with **no coordinates** — a script geocodes and populates the database
afterward. The app consumes two clean CSVs exported at the end of the
pipeline: `dal-platforms.csv` and `worker-location.csv`.

## Layout

```
dal-data/
├── data/
│   ├── raw - dal-platforms.csv      <- CSV export of the "dal-platforms" sheet
│   └── raw - worker-locations.csv   <- CSV export of the "worker-locations" sheet
├── schema.sql              <- table definitions, with comments
├── scripts/
│   ├── build_db.py         <- validates + rebuilds both tables from the raw CSVs
│   ├── geocode.py          <- fetches lat/lng for new places, caches them, fills rows
│   └── export.py           <- writes the two CSVs the app reads
├── dal.sqlite              <- the database itself (generated)
├── dal-platforms.csv       <- app input (generated)
└── worker-location.csv     <- app input (generated)
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
   Fully rebuilds `dal_platforms` and `worker_locations` from the CSVs
   (lat/lng left empty for now). It prints any duplicate platform names,
   any `worker-locations.platform` that doesn't exactly match a platform
   name, and any non-canonical country spellings (e.g. `USA` vs
   `United States`). Fix those in the sheets and re-run.
3. **Geocode anything new:**
   ```bash
   export LOCATIONIQ_API_KEY="your-token-here"   # one-time per shell
   python3 scripts/geocode.py
   ```
   Only fetches places not already in `geocode_cache`, then writes the
   lat/lng back onto every row in both tables. Sign up free at
   https://locationiq.com for a token.
4. **Export the CSVs the app reads:**
   ```bash
   python3 scripts/export.py
   ```
   Writes `dal-platforms.csv` and `worker-location.csv`. Copy these into
   the DAL Map app (e.g. `dal-map/public/data/`) or push them to wherever
   the app currently reads from.

## Why the tables get rebuilt but geocode_cache doesn't

`dal_platforms` and `worker_locations` hold nothing you haven't already
put in the spreadsheets — there's no reason to "merge" or "update" them
carefully, just regenerate them fresh every run and let SQLite hand out
new IDs.

`geocode_cache` is different: it holds the result of real API calls, keyed
on the query string itself (`"City, Country"` or `"Country"`), not on any
row or ID. That key stays valid no matter how IDs shuffle on a rebuild,
which is what makes the cache safe to keep forever.

Note: if you edit a row's `city`, its query string changes, so it'll miss
the cache and trigger one fresh LocationIQ call on the next run. That's
expected, not a bug.

## If a place can't be geocoded automatically

`geocode.py` prints a ready-to-paste `sqlite3` command to insert the
coordinates by hand once you've looked them up — done once per unique
place, then re-run `geocode.py` to join the new coordinates onto the rows.
