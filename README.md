# Galamsey Watch

Daily satellite monitoring of illegal small-scale mining (galamsey) across Ghana.
Free data watches the whole country; paid data confirms. Every alert is reviewed by a person
before it is treated as fact.

**Status:** all 12 milestones are built. Unit tests pass, and the web app builds. Nothing has run
against live services yet: no Supabase project, Earth Engine account, GFW key or messaging
provider has been connected. See *Not yet verified* at the end.

## How it works

```
05:00 GMT  GitHub Actions → pipeline (Python)
           connectors (agency feeds) → GFW alerts → Sentinel-2 + Sentinel-1 change per 10 km tile
           → fusion (one alert per site) → spatial context → AI classifier → score 0–100 → image chips
07:00 GMT  new Red/Amber alerts → WhatsApp / SMS / email (daily cap per region), daily summary email
Mondays    Watch-level digest + trends
Any time   Next.js dashboard on Vercel: map, alert review, field mode, tip line webhook, evidence PDFs,
           stats, admin; public map of confirmed sites (30-day delay)
Database   Supabase Postgres + PostGIS, row-level security on every table, audit trail by trigger
```

| Folder | What it is |
|---|---|
| `supabase/migrations/` | Schema, RLS, audit trigger, map/stat/review functions, storage buckets |
| `pipeline/galamsey/ingest_reference/` | Boundaries, forest reserves, rivers (+buffers), concessions, priority zones, grid |
| `pipeline/galamsey/detect/` | GFW (A), Sentinel-2 + turbidity (B, Earth Engine or Copernicus openEO), Sentinel-1 (C), Planet (paid, off) |
| `pipeline/galamsey/ai/` | Amazon Mining Watch classifier adapter (D) and Ghana retraining |
| `pipeline/galamsey/connectors/` | Government feeds (E) with mock data |
| `pipeline/galamsey/{fusion,context,scoring}.py` | Merge detections, find reserve/river/concession, score and level |
| `pipeline/galamsey/notify/` | One `notify()` for WhatsApp, SMS (Africa's Talking/Hubtel), email (Resend) |
| `pipeline/galamsey/daily.py` | The scheduled jobs |
| `pipeline/galamsey/validate.py` | Known-site check and precision sample |
| `web/` | Next.js 16 dashboard, tip webhook, evidence PDFs, public map |
| `docs/` | Agency data requests, costs and approvals |

## Roles

| Role | Can do |
|---|---|
| Admin | Users and roles, thresholds, recipients, data sources, pipeline runs, paid-data approvals |
| Analyst | Review alerts (confirm/reject), assign scouts, labels, evidence packs, stats |
| Field scout | See alerts in their regions, submit geotagged photo reports (works offline), mark field-verified |
| Public | `/public`: confirmed sites only, 30 days late, month-level dates, no scout or tip data |

## Setup

### 1. Supabase
1. Create a project (free plan). Apply `supabase/migrations/*.sql` in order (`supabase db push`, or paste into the SQL editor).
2. Auth: keep Email on, turn **off** public sign-ups (staff are invited). Add `<your-url>/auth/callback` to redirect URLs.
3. Invite yourself from Auth → Users, sign in once, then:
   ```sql
   update profiles set role = 'admin' where user_id = (select id from auth.users where email = 'you@example.com');
   ```

### 2. Pipeline
```bash
cp .env.example .env               # fill in at least SUPABASE_DB_URL, SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, GFW_API_KEY
cd pipeline && python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt      # add requirements-ai.txt for the AI classifier (TensorFlow, ~600 MB)
pytest -q                                # 37 tests, no network
python -m galamsey.ingest_reference all  # reference layers + 10 km grid (monthly)
python -m galamsey.daily detect          # one detection run
python -m galamsey.daily notify          # sends nothing while NOTIFY_DRY_RUN=1 (default): logs instead
```
Earth Engine: create a Google Cloud service account registered for Earth Engine and put its JSON in
`GEE_SERVICE_ACCOUNT_KEY`. Without it, Sources B and C use Copernicus openEO if `CDSE_CLIENT_ID` is set,
otherwise they are skipped and logged (GFW still runs).

### 3. Web
```bash
cd web && cp ../.env.example .env.local   # keep NEXT_PUBLIC_*, SUPABASE_SERVICE_ROLE_KEY, APP_URL, WHATSAPP_*, TIP_HASH_SALT
npm install && npm run dev                # http://localhost:3000
npm test && npm run build
```
No Supabase keys = demo mode (real 10 km grid, no data), clearly labelled.
Deploy: Vercel → import repo → root directory `web` → add the same variables.

### 4. Tip line (WhatsApp)
Meta developer app → WhatsApp → Configuration → webhook URL `https://<your-app>/api/whatsapp/webhook`,
verify token = `WHATSAPP_VERIFY_TOKEN`, subscribe to `messages`. The bot replies with a reference code,
groups messages from one sender for 30 minutes, links to any alert within 2 km, rate-limits (10/hour),
filters spam, and stores only a salted hash of the number.

### 5. Schedules
Add the variables as GitHub Actions secrets/variables. `daily.yml` runs detect (05:00), notify (07:00)
and the weekly digest (Mondays). `reference-refresh.yml` reloads reference data monthly. `ci.yml` runs
tests, lint, build and a secret scan on every push.

## Scoring (editable in Admin → Thresholds)

| Factor | Points |
|---|---|
| Base (detection ≥ 0.1 ha) | +20 |
| Each extra independent satellite family agreeing | +15 (max +30) |
| AI classifier probability | up to +25 |
| Size ≥ 0.3 / 1 / 5 ha | +5 / +10 / +15 |
| Seen again on a later pass | +10 |
| In forest reserve or ≤ 100 m from a major river | +20 (≤ 500 m: +8) |
| Outside any concession / inside a licensed concession | +10 / −30 |
| Cloud-affected or single low-confidence source | −10 |

Red = score ≥ 75 and in a reserve or river buffer. Amber = ≥ 50 and not in a concession. Watch = ≥ 30
(weekly digest only). At most 15 alerts per region per day are sent, highest scores first.
Two detectors on the same satellite (e.g. GLAD-S2 and our Sentinel-2) count as one family.

## Security

- RLS on every table. Field scouts see only their regions; signed-out visitors see only the delayed public function.
- New accounts start as field scouts with no regions; only admins change roles; admins can't demote themselves.
- Alert status changes are validated and logged by a database trigger (`alert_status_history`).
- Reviews go through one database function that records the decision, moves the status and saves a training label.
- The tip webhook checks Meta's HMAC signature, downloads media only from Meta hosts, hashes numbers, and never logs them.
- Storage buckets are private; the app serves 1-hour signed URLs.
- Secrets only in env vars; `.env` is git-ignored and CI runs gitleaks. Security headers are set in `next.config.ts`.

## Validation (before trusting alerts)
```bash
python -m galamsey.validate known-sites        # Atewa, Birim, Offin, Pra, Ankobra reference areas
python -m galamsey.validate sample --n 50      # hand-check these in the dashboard
python -m galamsey.validate report --ids sample.csv   # precision of the sample
GEE_LOCAL_AUTH=1 pytest -m integration         # end-to-end test on Atewa
```

## Not yet verified
- **Database:** migrations have been syntax-checked, not applied to a live PostGIS database. RLS policies,
  triggers and SQL functions need a test run on Supabase.
- **Earth Engine / Copernicus / GFW / messaging providers:** the code follows their published APIs but has not
  made a live call. GFW dataset field names should be checked on the first run.
- **AI classifier:** the model download and TensorFlow inference have not been run here; retraining needs 300 labels.
- **Detection quality:** precision on Ghana is unknown until the 50-alert validation is done.

Licences, costs and legal notes: `docs/costs-and-approvals.md`. Agency requests: `docs/agency-data-requests.md`.
