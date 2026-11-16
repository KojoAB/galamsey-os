# Costs, approvals and licences

Everything runs on free tiers until you approve each paid item. Nothing paid switches on by itself.

| Item | Free? | What it costs | Switch |
|---|---|---|---|
| Supabase | Free plan: 500 MB database, 1 GB storage, projects pause after a week idle | Pro plan from about $25/month (no pausing, daily backups) | — |
| Vercel (dashboard) | Hobby plan is free but **non-commercial** | Pro plan per seat if used commercially or by an organisation | — |
| GitHub Actions | 2,000 free minutes/month on private repos; unlimited on public repos | The daily run may need more than the free minutes on a private repo | — |
| Global Forest Watch API | Free key | — | `GFW_API_KEY` |
| Google Earth Engine | Free for non-commercial and research use | Commercial/operational use needs a paid licence; confirm with Google | `GEE_SERVICE_ACCOUNT_KEY` |
| Copernicus Data Space (fallback) | Free account with monthly processing quota | — | `CDSE_CLIENT_ID` |
| Resend email | 3,000 emails/month free | Paid above that | `RESEND_API_KEY` |
| SMS (Africa's Talking / Hubtel) | No | Per message, prepaid | `NOTIFY_DRY_RUN=0` |
| WhatsApp Cloud API | Replies to tipsters within 24 h are free; alerts we start use paid template messages | Per message, billed by Meta | `NOTIFY_DRY_RUN=0` |
| Planet (PlanetScope, tasking) | No | Subscription per km² plus tasking fees | `PLANET_ENABLED=1` **and** admin setting `planet_enabled` |
| NICFI Planet basemaps (archive 2015–2025) | Free, non-commercial licence | — | Check the licence before using chips for training |

Licences recorded in the data: every reference layer stores `source`, `retrieved_at` and `licence`.
The Amazon Mining Watch model is MIT-licensed (Earth Genome). WDPA is non-commercial.

Legal notes to check with a Ghanaian lawyer before launch (not legal advice):
- Data Protection Act, 2012 (Act 843): register with the Data Protection Commission as a data controller;
  staff and field-scout data is personal data. Tipsters' numbers are never stored (salted hash only).
- Publishing named sites: the public map shows only analyst-confirmed sites, delayed 30 days, month-level
  dates, no scout or tip data. Get advice on defamation risk before naming concession holders.
- Agency data: follow each agency's written data-sharing terms.
