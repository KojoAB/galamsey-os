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
