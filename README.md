# DAL research database

Canonical source of truth for DAL company/location data, separate from
the DAL Map app repo. The app only ever consumes `summary.csv`, exported
at the bottom of this pipeline.

## Layout

```
dal-research/
├── data/
│   └── workers.csv        <- CSV export of your "workers" Google Sheet tab
├── schema.sql              <- table definitions, with comments
├── scripts/
│   ├── build_db.py         <- rebuilds companies + locations from workers.csv
│   ├── geocode.py           <- fetches lat/long for new places, caches them
│   └── export_summary.py    <- joins everything into summary.csv
├── dal.sqlite               <- the database itself (generated)
└── summary.csv               <- the file the React app actually reads (generated)
```

## One-time setup

```bash
git init
python3 -m venv .venv
source .venv/bin/activate        # on Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Running the pipeline

Every time you've added or edited rows in the "workers" Google Sheet:

1. **Export the sheet to CSV** and overwrite `data/workers.csv`.
2. **Rebuild the database:**
   ```bash
   python3 scripts/build_db.py
   ```
   This fully rebuilds `companies` and `locations` from the CSV. It'll
   print any rows it couldn't safely auto-clean into a geocode query —
   fix those in the sheet (add a clean city in parentheses, matching
   the convention already used elsewhere, e.g. `TaskUs Colombia SAS (Cali)`)
   and re-run.
3. **Geocode anything new:**
   ```bash
   python3 scripts/geocode.py
   ```
   Only fetches places not already in `geocode_cache` — re-running this
   after adding 3 new rows costs 3 API calls, not 69. Edit `CONTACT` in
   the script before your first run, per Nominatim's usage policy.
4. **Export the final summary:**
   ```bash
   python3 scripts/export_summary.py
   ```
   Writes `summary.csv`. Copy this into the DAL Map app repo (e.g.
   `dal-map/public/data/summary.csv`) or push it to your published
   Google Sheet — whichever your app currently reads from.

## Why companies/locations get fully rebuilt but geocode_cache doesn't

`companies` and `locations` hold nothing you haven't already put in the
spreadsheet — there's no reason to "merge" or "update" them carefully,
just regenerate them fresh every time and let SQLite hand out new IDs.

`geocode_cache` is different: it holds the result of real API calls,
keyed on the place name text itself (not on any row or company ID). That
key stays valid no matter how IDs shuffle around on a rebuild, which is
exactly what makes the cache safe to keep forever.

## If a place can't be geocoded automatically

`geocode.py` will print a `sqlite3` command you can run to insert the
coordinates by hand once you've looked them up — same as you do today,
just done once per unique place instead of once per row.
