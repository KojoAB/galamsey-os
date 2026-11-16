# Galamsey Watch

Daily satellite monitoring of illegal small-scale mining (galamsey) across Ghana.
Free data watches the whole country; paid data confirms. Every alert is reviewed by a person
before it is treated as fact.

**Status: Milestone 1 — Foundation.** Schema, reference-data loaders, national tile grid and a
basic map. No detection or alerts yet (Milestone 2).

## Repository layout

| Folder | What it is |
|---|---|
| `supabase/migrations/` | PostGIS schema, row-level security, audit trail, map functions |
| `pipeline/` | Python 3.11+ processing: reference-data loaders, tile grid (later: detection) |
| `pipeline/config/reference.yaml` | Editable lists: major rivers to buffer, mining-belt priority districts |
| `web/` | Next.js dashboard (MapLibre map, Supabase Auth) |
| `.github/workflows/` | CI tests, monthly reference-data refresh |
| `docs/` | Agency data requests and design notes |

## 1. Set up Supabase (free tier)

1. Create a project at supabase.com (free plan). Region: closest available to Ghana (e.g. EU West / Frankfurt).
2. Apply the migrations, either with the Supabase CLI:
   ```bash
   supabase link --project-ref <your-ref>
   supabase db push
   ```
   or by pasting the three files in `supabase/migrations/` (in order) into the SQL editor.
3. Auth → Sign In / Providers: keep Email on. Turn **off** "Allow new users to sign up" — staff are invited.
4. Auth → URL configuration: add `http://localhost:3000/auth/callback` and your Vercel URL `/auth/callback`.
5. Invite yourself (Auth → Users → Invite), sign in once, then make yourself admin in the SQL editor:
   ```sql
   update profiles set role = 'admin', regions = '{Western,"Western North",Ashanti,Eastern,Central}'
   where user_id = (select id from auth.users where email = 'you@example.com');
   ```

## 2. Load reference data and the grid

```bash
cp .env.example .env            # fill in SUPABASE_DB_URL at minimum
cd pipeline
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q                                     # 11 tests, no network needed
python -m galamsey.ingest_reference all --out ./out   # dry run: GeoJSON files only, no database
python -m galamsey.ingest_reference all               # load into Supabase
```

Order inside `all`: boundaries → forest reserves → rivers (+100 m / 500 m buffers) → concessions →
priority zones → tile grid. Each step is safe to re-run; a layer is replaced only after its new
version has loaded completely. Every run is logged in `pipeline_runs`.

**Where each layer comes from** (first available wins; the source is stored on every row):

| Layer | Official source (verified) | Open fallback |
|---|---|---|
| Regions & districts | Ghana Statistical Service | geoBoundaries gbOpen |
| Forest reserves | Forestry Commission | WDPA (needs free token) → OpenStreetMap |
| Rivers | Water Resources Commission | OpenStreetMap → HydroRIVERS (local file) |
| Concessions | Minerals Commission cadastre | Analyst import (below) |
| Priority zones | — | Built from `config/reference.yaml` district list |

Import a concession file until the cadastre feed is agreed:
```bash
python -m galamsey.ingest_reference import-concessions mines.geojson --source "analyst:kojo" --date 2026-10-01
```
Only files that came from the Minerals Commission may use `--verified` (with `--source minerals_commission:...`).

**Add or change a priority region:** edit `priority_districts` in `pipeline/config/reference.yaml`, then
run `python -m galamsey.ingest_reference priority-zones` and `... grid`. The run log lists any district
name it could not match so you can correct the spelling.

## 3. Run the map

```bash
cd web
cp ../.env.example .env.local     # keep only the NEXT_PUBLIC_ lines, fill them in
npm install
npm run dev                       # http://localhost:3000
```
With no Supabase keys (or `NEXT_PUBLIC_DEMO_MODE=1`) the map runs in demo mode on the real
10 km grid from `public/demo/`, clearly labelled.

Deploy: import the repo in Vercel, set the root directory to `web`, add the two `NEXT_PUBLIC_` variables.

## 4. Automation

- `ci.yml` runs pipeline tests and the web build on every push.
- `reference-refresh.yml` reloads reference data on the 1st of each month. Add `SUPABASE_DB_URL`
  (and any agency URLs/tokens) as GitHub Actions secrets.

## Security notes

- Row-level security is on for every table. Analysts/admins see everything; field scouts see only alerts
  in their regions; signed-out visitors see nothing (the delayed public map arrives in M11).
- New accounts start as `field_scout` with no regions; only an admin can change roles.
- Alert status changes are validated (`new → reviewing → confirmed | rejected → field_verified → closed`)
  and every change is written to `alert_status_history` by a database trigger.
- Tip-line phone numbers are never stored, only a salted hash (M7).
- The pipeline's `SUPABASE_DB_URL` bypasses RLS; keep it in `.env` / GitHub secrets only.

## Licences and attribution

Each layer stores its `source`, `retrieved_at` and `licence`. The map footer shows the required
attributions (Copernicus, Global Forest Watch, geoBoundaries, WDPA, OpenStreetMap, HydroSHEDS, Esri).
WDPA is free for non-commercial use; confirm terms before any commercial use.
