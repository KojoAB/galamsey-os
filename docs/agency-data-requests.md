# Agency data requests (reference layers)

What to ask each agency for so their data can replace the open fallbacks. Any format below works;
the loader reads a URL or a file. Ask for the data-sharing terms in writing, including whether
the layer may appear on a **public** map.

| Agency | Ask for | Preferred format | Refresh | Env variable |
|---|---|---|---|---|
| Ghana Statistical Service / Survey & Mapping Division | Official region (ADM1) and district (ADM2) boundaries, current 261-district set | GeoJSON or zipped Shapefile, WGS84, with district names and codes | When districts change | `GSS_BOUNDARIES_ADM1_URL`, `GSS_BOUNDARIES_ADM2_URL` |
| Forestry Commission (RMSC) | Forest reserve and protected-area boundaries with reserve names; their mapped mining-damage areas (used later as detection source and training labels) | Shapefile / GeoPackage | Quarterly | `FORESTRY_COMMISSION_RESERVES_URL` |
| Water Resources Commission | River network and basin boundaries (Pra, Ankobra, Tano, Densu, Volta systems), water-quality station locations and readings | Shapefile; station readings as CSV or API | Monthly | `WRC_RIVERS_URL` |
| Minerals Commission | Mining cadastre: licence polygons with licence number, holder, type (large/small-scale), status (active/expired/suspended), dates. Ideally a read-only API or scheduled export | GeoJSON/Shapefile export or API | Weekly | `MINERALS_COMMISSION_CADASTRE_URL` |

Excavator GPS tracking, CERSGIS detections and NAIMOS outcomes are Milestone 10 connectors and
will get their own request list then.

## Live connectors (Milestone 10)

Each connector is built and tested on mock data (`pipeline/galamsey/connectors/mock/`). To switch one
to real data, set `<ID>_URL` (and `<ID>_TOKEN` if needed) and remove `<ID>_MOCK`. The admin panel's
*Data sources* tab shows each one as connected, stale or off.

| Connector id | Agency | What to request | Format we expect (one record) |
|---|---|---|---|
| `minerals_commission_cadastre` | Minerals Commission | Read-only API or nightly export of the mining cadastre | GeoJSON Feature, properties `licence_no, holder, status (active/expired/suspended), licence_type, expires` |
| `minerals_commission_equipment` | Minerals Commission | Read-only API to the excavator/equipment GPS tracking system, positions since a timestamp | `{device_id, lon, lat, recorded_at (ISO 8601)}` |
| `forestry_commission_mining` | Forestry Commission (RMSC) | Their mapped mining-damage polygons, updated monthly | GeoJSON Feature, properties `id, date, confidence (0–1)` |
| `cersgis_nrt` | CERSGIS | Their near-real-time mining detections | Same as Forestry Commission |
| `naimos` | NAIMOS | Two-way: an endpoint to receive our confirmed alerts (`POST /alerts`) and a feed of operation outcomes | Outcome: `{alert_id, action (raided/closed/reclaimed/arrests), action_date, notes}` |
| `wrc_water_quality` | Water Resources Commission | Station list and turbidity readings | `{station_id, station_name, lon, lat, measured_at, turbidity_ntu}` |

Credentials to ask for: a service account (not a person's login), read-only scope, an API token we
can rotate, and an IP allow-list exception for GitHub Actions if they use one. Agency data stays off
the public map unless the agency agrees in writing (`connectors.public_allowed`).
