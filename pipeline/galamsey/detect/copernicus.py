"""Fallback for Source B when Earth Engine is unavailable or its licence blocks
operational use: Copernicus Data Space Ecosystem (free account) via openEO.

openEO builds the cloud-masked new image and 90-day median baseline on the
Copernicus side and returns a small GeoTIFF per tile; the change rule itself runs
here in numpy (indices.py), so both back-ends use identical logic.

Credentials: CDSE_CLIENT_ID / CDSE_CLIENT_SECRET (an OIDC client from
https://dataspace.copernicus.eu → User settings → OAuth clients).
"""

from __future__ import annotations

import os
import tempfile
from datetime import date, timedelta

import numpy as np

from . import indices as ix

BANDS = ["B02", "B03", "B04", "B08", "B11", "SCL"]
BAD_SCL = [0, 1, 3, 8, 9, 10, 11]


def _connect():
    import openeo

    conn = openeo.connect("openeo.dataspace.copernicus.eu")
    cid, secret = os.environ.get("CDSE_CLIENT_ID"), os.environ.get("CDSE_CLIENT_SECRET")
    if cid and secret:
        conn.authenticate_oidc_client_credentials(client_id=cid, client_secret=secret)
    else:
        conn.authenticate_oidc()
    return conn


def _fetch(conn, bbox: dict, start: date, end: date, reducer: str) -> tuple[np.ndarray, object, object]:
    import rasterio

    cube = conn.load_collection("SENTINEL2_L2A", spatial_extent=bbox,
                                temporal_extent=[start.isoformat(), end.isoformat()], bands=BANDS)
    scl = cube.band("SCL")
    bad = scl == BAD_SCL[0]
    for v in BAD_SCL[1:]:
        bad = bad | (scl == v)
    cube = cube.filter_bands(BANDS[:-1]).mask(bad).reduce_dimension("t", reducer=reducer)
    with tempfile.NamedTemporaryFile(suffix=".tif", delete=False) as fh:
        cube.download(fh.name, format="GTiff")
        with rasterio.open(fh.name) as src:
            return src.read().astype("float64") / 10000, src.transform, src.crs


def detect_tile(tile_geom, scene_day: date, tile_id: str) -> list[dict]:
    from rasterio.features import shapes
    from shapely.geometry import shape
    import geopandas as gpd

    minx, miny, maxx, maxy = tile_geom.bounds
    bbox = {"west": minx, "south": miny, "east": maxx, "north": maxy}
    conn = _connect()
    new, transform, crs = _fetch(conn, bbox, scene_day, scene_day + timedelta(days=1), "first")
    base, _, _ = _fetch(conn, bbox, scene_day - timedelta(days=90), scene_day, "median")
    b, g, r, n, s = new
    B, G, R, N, S = base
    mask = ix.change_mask(ix.ndvi(n, r), ix.ndvi(N, R), ix.bsi(b, r, n, s), ix.bsi(B, R, N, S),
                          ix.mndwi(g, s), ix.mndwi(G, S))
    lab = ix.label_clusters(mask, min_pixels=10)
    polys = [shape(geom) for geom, val in shapes(lab.astype("int32"), mask=lab > 0, transform=transform) if val > 0]
    if not polys:
        return []
    gdf = gpd.GeoDataFrame(geometry=polys, crs=crs).dissolve().explode(index_parts=False).to_crs("EPSG:4326")
    out = []
    for geom in gdf.geometry:
        rep = geom.representative_point()
        out.append({"source": "s2_change", "geometry": geom, "image_date": scene_day, "confidence": 0.6,
                    "tile_id": tile_id, "metrics": {"backend": "copernicus_openeo"},
                    "external_id": f"s2|{tile_id}|{scene_day}|{rep.x:.4f}|{rep.y:.4f}"})
    return out
