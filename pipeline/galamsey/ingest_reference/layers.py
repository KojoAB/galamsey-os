"""Source chains for each reference layer (spec section 4).

Order inside each list = preference. Agency sources come first and are only used
when their URL/path is set in .env; open data is the fallback.
"""

from __future__ import annotations

import geopandas as gpd
import osm2geojson
from shapely.geometry import LineString

from ..config import settings
from .sources import (GHANA_AREA, OSM_LICENCE, Source, SourceUnavailable,
                      from_file_or_url, http_get_json, overpass)

# ---------------------------------------------------------------------------
# 1. Boundaries — Ghana Statistical Service → geoBoundaries
# ---------------------------------------------------------------------------

def _geoboundaries(level: str):
    def fetch() -> gpd.GeoDataFrame:
        meta = http_get_json(f"https://www.geoboundaries.org/api/current/gbOpen/GHA/{level}/")
        gdf = gpd.read_file(meta["gjDownloadURL"]).rename(columns={"shapeName": "name"})
        # The exact licence varies per boundary set; record the one geoBoundaries publishes.
        if meta.get("boundaryLicense"):
            gdf.attrs["licence"] = f"geoBoundaries gbOpen — {meta['boundaryLicense']} — Runfola et al. 2020"
        return gdf
    return fetch


def boundary_sources(level: str) -> list[Source]:
    official = {"ADM1": settings.gss_boundaries_adm1, "ADM2": settings.gss_boundaries_adm2}.get(level)
    return [
        Source(f"ghana_statistical_service:{level.lower()}", "Per GSS data-sharing terms", True,
               from_file_or_url(official)),
        Source(f"geoboundaries:gbOpen:{level}",
               "geoBoundaries gbOpen (CC BY 4.0 / ODbL per boundary) — Runfola et al. 2020", False,
               _geoboundaries(level)),
    ]


# ---------------------------------------------------------------------------
# 2. Forest reserves — Forestry Commission → WDPA (Protected Planet) → OpenStreetMap
# ---------------------------------------------------------------------------

def _wdpa() -> gpd.GeoDataFrame:
    """Protected Planet API v3. Needs a free token (request at api.protectedplanet.net)."""
    if not settings.protected_planet_token:
        raise SourceUnavailable("PROTECTED_PLANET_API_TOKEN not set")
    features, page = [], 1
    while True:
        data = http_get_json("https://api.protectedplanet.net/v3/protected_areas/search",
                             token=settings.protected_planet_token, country="GHA",
                             with_geometry="true", per_page=50, page=page)
        batch = data.get("protected_areas", [])
        for pa in batch:
            geo = pa.get("geojson")
            if geo and geo.get("geometry"):
                features.append({"type": "Feature", "geometry": geo["geometry"],
                                 "properties": {"name": pa.get("name"), "wdpa_id": pa.get("id"),
                                                "designation": (pa.get("designation") or {}).get("name")}})
        if len(batch) < 50:
            break
        page += 1
    if not features:
        raise SourceUnavailable("WDPA returned no polygons for GHA")
    return gpd.GeoDataFrame.from_features(features, crs="EPSG:4326")


def _osm_forest_reserves() -> gpd.GeoDataFrame:
    q = f"""[out:json][timeout:300];{GHANA_AREA}
    ( relation["boundary"="protected_area"](area.gh);
      way["boundary"="protected_area"](area.gh);
      relation["leisure"="nature_reserve"](area.gh);
      way["leisure"="nature_reserve"](area.gh);
      relation["landuse"="forest"]["name"~"Forest Reserve|F\\\\.R\\\\.",i](area.gh);
      way["landuse"="forest"]["name"~"Forest Reserve|F\\\\.R\\\\.",i](area.gh); );
    out geom;"""
    fc = osm2geojson.json2geojson(overpass(q))
    polys = [f for f in fc["features"] if f["geometry"]["type"] in ("Polygon", "MultiPolygon")]
    for f in polys:
        f["properties"] = {"name": (f["properties"].get("tags") or {}).get("name"),
                           "osm_id": f["properties"].get("id")}
    if not polys:
        raise SourceUnavailable("OSM returned no protected-area polygons")
    return gpd.GeoDataFrame.from_features(polys, crs="EPSG:4326")


def forest_reserve_sources() -> list[Source]:
    return [
        Source("forestry_commission:reserves", "Per Forestry Commission data-sharing terms", True,
               from_file_or_url(settings.forestry_commission_reserves)),
        Source("wdpa:protectedplanet", "WDPA — UNEP-WCMC & IUCN, Protected Planet (non-commercial terms)", False, _wdpa),
        Source("osm:protected_areas", OSM_LICENCE, False, _osm_forest_reserves),
    ]


# ---------------------------------------------------------------------------
# 3. Rivers — Water Resources Commission → OpenStreetMap → HydroRIVERS (local file)
# ---------------------------------------------------------------------------

def osm_rivers_from_overpass(payload: dict) -> gpd.GeoDataFrame:
    """Convert Overpass 'out geom' ways into LineStrings. Kept separate so it can be unit-tested."""
    rows = []
    for el in payload.get("elements", []):
        if el.get("type") != "way" or len(el.get("geometry", [])) < 2:
            continue
        coords = [(p["lon"], p["lat"]) for p in el["geometry"]]
        tags = el.get("tags", {})
        rows.append({"name": tags.get("name"), "waterway": tags.get("waterway"),
                     "osm_id": el["id"], "geometry": LineString(coords)})
    if not rows:
        raise SourceUnavailable("OSM returned no river ways")
    return gpd.GeoDataFrame(rows, crs="EPSG:4326")


def _osm_rivers(names: list[str]):
    def fetch() -> gpd.GeoDataFrame:
        pattern = "|".join(names)
        q = f"""[out:json][timeout:300];{GHANA_AREA}
        way["waterway"~"^(river|stream)$"]["name"~"^({pattern})( River)?$",i](area.gh);
        out geom;"""
        return osm_rivers_from_overpass(overpass(q))
    return fetch


def _hydrorivers() -> gpd.GeoDataFrame:
    """HydroRIVERS Africa (download from hydrosheds.org, set HYDRORIVERS_PATH). Unnamed, so tagged by order."""
    if not settings.hydrorivers_path:
        raise SourceUnavailable("HYDRORIVERS_PATH not set")
    gdf = gpd.read_file(settings.hydrorivers_path, bbox=(-3.3, 4.7, 1.3, 11.2))
    gdf = gdf[gdf["ORD_STRA"] >= 4]          # Strahler order 4+ = main rivers and large tributaries
    gdf["name"] = "HydroRIVERS order " + gdf["ORD_STRA"].astype(str)
    return gdf


def river_sources(names: list[str]) -> list[Source]:
    return [
        Source("water_resources_commission:rivers", "Per WRC data-sharing terms", True,
               from_file_or_url(settings.wrc_rivers)),
        Source("osm:waterways", OSM_LICENCE, False, _osm_rivers(names)),
        Source("hydrosheds:hydrorivers", "HydroSHEDS licence (free, attribution required) — Lehner & Grill 2013", False,
               _hydrorivers),
    ]


# ---------------------------------------------------------------------------
# 4. Licensed concessions — Minerals Commission cadastre → analyst upload (see cli import-concessions)
# ---------------------------------------------------------------------------

def concession_sources() -> list[Source]:
    return [
        Source("minerals_commission:cadastre", "Per Minerals Commission data-sharing terms", True,
               from_file_or_url(settings.minerals_commission_cadastre)),
    ]
