"""Shared geospatial helpers.

Why two coordinate systems?
- WGS84 (EPSG:4326, longitude/latitude) is what maps and the database store.
- UTM zone 30N (EPSG:32630) measures in metres, so areas, buffers and the
  10 km grid are computed there. Ghana sits almost entirely in zone 30, and the
  small eastern strip in zone 31 is distorted by well under 1% — fine for this use.
"""

from __future__ import annotations

import geopandas as gpd
from shapely.geometry.base import BaseGeometry

WGS84 = "EPSG:4326"
UTM30N = "EPSG:32630"


def to_metric(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    return gdf.to_crs(UTM30N)


def to_wgs84(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    return gdf.to_crs(WGS84)


def clean(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Drop empty geometries and repair invalid ones (self-intersections are common in agency files)."""
    if gdf.crs is None:
        gdf = gdf.set_crs(WGS84)
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty].copy()
    gdf["geometry"] = gdf.geometry.make_valid()
    return gdf[~gdf.geometry.is_empty]


def buffer_metres(gdf: gpd.GeoDataFrame, metres: float, dissolve_by: str | None = None) -> gpd.GeoDataFrame:
    """Buffer features by a distance in metres and return them in WGS84.

    With dissolve_by, features sharing that column (e.g. all segments of the Birim)
    are merged into one buffer polygon, which keeps the layer small.
    """
    metric = to_metric(gdf)
    if dissolve_by:
        metric = metric.dissolve(by=dissolve_by, as_index=False)
    metric["geometry"] = metric.geometry.buffer(metres, quad_segs=8)
    return to_wgs84(metric)


def area_ha(geom: BaseGeometry, crs: str = WGS84) -> float:
    s = gpd.GeoSeries([geom], crs=crs).to_crs(UTM30N)
    return float(s.area.iloc[0] / 10_000)
