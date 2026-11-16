"""Source A — Global Forest Watch ready-made alerts (GLAD-L, GLAD-S2, RADD, DIST-ALERT).

GFW's Data API returns alert *pixels* (lat/lon + date + confidence). We turn each
pixel into a small square, merge neighbours into clearings, and keep clearings of
0.1 ha or more. Free API key: https://www.globalforestwatch.org/my-gfw → API keys.

Dataset and field names follow the GFW Data API catalogue. If GFW renames a
dataset, only DATASETS below needs to change.
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from datetime import date

import geopandas as gpd
import pandas as pd
import requests
from shapely.geometry import box, mapping

from ..geo import UTM30N, WGS84

log = logging.getLogger(__name__)
API = "https://data-api.globalforestwatch.org"


@dataclass(frozen=True)
class GfwDataset:
    source: str        # our source tag
    dataset: str       # GFW dataset id
    field: str         # field prefix in the table
    pixel_m: int       # native pixel size
    confidences: dict  # GFW confidence label → 0–1


DATASETS = [
    GfwDataset("gfw_glad_l", "umd_glad_landsat_alerts", "umd_glad_landsat_alerts", 30,
               {"nominal": 0.5, "high": 0.8}),
    GfwDataset("gfw_glad_s2", "umd_glad_sentinel2_alerts", "umd_glad_sentinel2_alerts", 10,
               {"nominal": 0.5, "high": 0.8}),
    GfwDataset("gfw_radd", "wur_radd_alerts", "wur_radd_alerts", 10,
               {"nominal": 0.5, "high": 0.8}),
    GfwDataset("gfw_dist", "umd_glad_dist_alerts", "umd_glad_dist_alerts", 30,
               {"low": 0.4, "nominal": 0.5, "high": 0.8, "highest": 0.9}),
]


def query_dataset(ds: GfwDataset, geometry: dict, since: date, api_key: str, timeout: int = 300) -> pd.DataFrame:
    sql = (f"SELECT latitude, longitude, {ds.field}__date AS alert_date, {ds.field}__confidence AS confidence "
           f"FROM results WHERE {ds.field}__date >= '{since.isoformat()}'")
    resp = requests.post(f"{API}/dataset/{ds.dataset}/latest/query/json",
                         json={"sql": sql, "geometry": geometry},
                         headers={"x-api-key": api_key, "Content-Type": "application/json"},
                         timeout=timeout)
    resp.raise_for_status()
    return pd.DataFrame(resp.json().get("data", []))


def pixels_to_clearings(df: pd.DataFrame, ds: GfwDataset, min_area_ha: float = 0.1) -> gpd.GeoDataFrame:
    """Alert pixels → merged clearing polygons with an image date and confidence.

    Pixels from the same week are merged when they touch. Each clearing keeps its
    latest alert date and its highest confidence.
    """
    cols = ["source", "image_date", "confidence", "pixels", "external_id", "geometry"]
    if df.empty:
        return gpd.GeoDataFrame(columns=cols, geometry="geometry", crs=WGS84)

    pts = gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df["longitude"], df["latitude"]), crs=WGS84).to_crs(UTM30N)
    half = ds.pixel_m / 2
    # Squares slightly larger than a pixel so diagonal neighbours also merge.
    pts["geometry"] = [box(p.x - half - 0.5, p.y - half - 0.5, p.x + half + 0.5, p.y + half + 0.5) for p in pts.geometry]
    pts["alert_date"] = pd.to_datetime(pts["alert_date"]).dt.date
    pts["week"] = pd.to_datetime(pts["alert_date"]).dt.to_period("W").astype(str)
    pts["conf"] = pts["confidence"].map(lambda c: ds.confidences.get(str(c).lower(), 0.5))

    out = []
    for week, grp in pts.groupby("week"):
        merged = grp.dissolve().explode(index_parts=False).reset_index(drop=True)
        for poly in merged.geometry:
            if poly.area / 10_000 < min_area_ha:
                continue
            inside = grp[grp.intersects(poly)]
            rep = poly.representative_point()
            ext = hashlib.sha1(f"{ds.source}|{week}|{rep.x:.0f}|{rep.y:.0f}".encode()).hexdigest()[:20]
            out.append({"source": ds.source, "image_date": inside["alert_date"].max(),
                        "confidence": float(inside["conf"].max()), "pixels": len(inside),
                        "external_id": ext, "geometry": poly})
    if not out:
        return gpd.GeoDataFrame(columns=cols, geometry="geometry", crs=WGS84)
    return gpd.GeoDataFrame(out, geometry="geometry", crs=UTM30N).to_crs(WGS84)


def fetch_gfw(area: gpd.GeoDataFrame, since: date, api_key: str | None = None,
              min_area_ha: float = 0.1) -> tuple[gpd.GeoDataFrame, list[str]]:
    """Pull all four alert systems for each polygon in `area` (e.g. one per region).

    Returns (clearings, errors). A failed dataset is skipped and reported, never fatal.
    """
    api_key = api_key or os.environ.get("GFW_API_KEY")
    if not api_key:
        return gpd.GeoDataFrame(columns=["geometry"], geometry="geometry", crs=WGS84), ["GFW_API_KEY not set"]
    frames, errors = [], []
    for ds in DATASETS:
        for geom in area.to_crs(WGS84).geometry:
            try:
                pixels = query_dataset(ds, mapping(geom.simplify(0.005)), since, api_key)
                frames.append(pixels_to_clearings(pixels, ds, min_area_ha))
            except (requests.RequestException, ValueError, KeyError) as exc:
                errors.append(f"{ds.source}: {exc}")
                log.warning("GFW %s failed: %s", ds.source, exc)
    frames = [f for f in frames if not f.empty]
    if not frames:
        return gpd.GeoDataFrame(columns=["geometry"], geometry="geometry", crs=WGS84), errors
    return gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs=WGS84), errors
