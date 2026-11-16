"""Source B — our own Sentinel-2 optical change detector (spec 5.3), on Earth Engine.

For each 10 km tile and each new Sentinel-2 L2A scene:
1. Mask clouds and shadows (Scene Classification Layer + Cloud Score+).
2. Skip the tile if more than 60% of it is cloudy.
3. Baseline = cloud-masked median of the previous 90 days.
4. Compute NDVI, BSI, MNDWI for the new image and the baseline (same formulas as indices.py).
5. Flag pixels where NDVI dropped > 0.25 and bare soil rose, or new water appeared on vegetated land.
6. Turn flagged pixels into polygons; keep clusters of ≥ 10 pixels (≈ 0.1 ha).
7. River turbidity: per river segment in the tile, compare mean NDTI with its own
   90-day history and flag sharp jumps.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from shapely.geometry import shape

from . import indices as ix
from .earthengine import ee, to_ee_geometry, vectors_to_records

log = logging.getLogger(__name__)

S2 = "COPERNICUS/S2_SR_HARMONIZED"
CS_PLUS = "GOOGLE/CLOUD_SCORE_PLUS/V1/S2_HARMONIZED"
# SCL classes to drop: 3 cloud shadow, 8/9 cloud, 10 cirrus, 11 snow, 0/1 no data / saturated
BAD_SCL = [0, 1, 3, 8, 9, 10, 11]


def _mask(img):
    e = ee()
    scl = img.select("SCL")
    good = scl.remap(BAD_SCL, [0] * len(BAD_SCL), 1)
    clear = img.select("cs_cdf").gte(0.6)
    return img.updateMask(good.And(clear)).divide(10000).copyProperties(img, ["system:time_start"])


def _with_indices(img):
    nd = img.normalizedDifference(["B8", "B4"]).rename("ndvi")
    mndwi = img.normalizedDifference(["B3", "B11"]).rename("mndwi")
    ndti = img.normalizedDifference(["B4", "B3"]).rename("ndti")
    bsi = img.expression("((S + R) - (N + B)) / ((S + R) + (N + B))",
                         {"S": img.select("B11"), "R": img.select("B4"),
                          "N": img.select("B8"), "B": img.select("B2")}).rename("bsi")
    return img.addBands([nd, mndwi, ndti, bsi])


def collection(region, start: date, end: date):
    e = ee()
    return (e.ImageCollection(S2).filterBounds(region).filterDate(start.isoformat(), end.isoformat())
            .linkCollection(e.ImageCollection(CS_PLUS), ["cs_cdf"]))


def new_scene_dates(tile_geom, since: date, until: date) -> list[date]:
    e = ee()
    col = collection(to_ee_geometry(tile_geom), since, until + timedelta(days=1))
    millis = col.aggregate_array("system:time_start").getInfo()
    return sorted({date.fromtimestamp(m / 1000) for m in millis})


def detect_tile(tile_geom, scene_day: date, tile_id: str, max_cloud_pct: float = 60,
                river_segments: list[dict] | None = None) -> tuple[list[dict], dict]:
    """Run the optical detector for one tile and one acquisition day.

    Returns (detections, stats). Each detection: source, geometry (shapely, WGS84),
    image_date, confidence, metrics, external_id.
    """
    e = ee()
    region = to_ee_geometry(tile_geom)
    day = collection(region, scene_day, scene_day + timedelta(days=1))
    raw = day.mosaic().clip(region)

    # Cloud fraction over the tile from the mask itself.
    masked = _mask(day.mosaic().set("system:time_start", e.Date(scene_day.isoformat()).millis()))
    clear_frac = masked.select("B4").mask().reduceRegion(
        e.Reducer.mean(), region, scale=60, maxPixels=1e8).get("B4").getInfo() or 0.0
    cloud_pct = round(100 * (1 - clear_frac), 1)
    stats = {"tile_id": tile_id, "date": scene_day.isoformat(), "cloud_pct": cloud_pct}
    if cloud_pct > max_cloud_pct:
        stats["skipped"] = "cloudy"
        return [], stats

    new = _with_indices(masked)
    base_col = collection(region, scene_day - timedelta(days=90), scene_day).map(_mask).map(_with_indices)
    base = base_col.median()

    drop = base.select("ndvi").subtract(new.select("ndvi")).gt(ix.NDVI_DROP)
    soil = new.select("bsi").subtract(base.select("bsi")).gt(ix.BSI_RISE)
    new_water = (new.select("mndwi").gt(ix.WATER_THRESHOLD)
                 .And(base.select("mndwi").lte(ix.WATER_THRESHOLD))
                 .And(base.select("ndvi").gt(ix.VEGETATED_NDVI)))
    flagged = drop.And(soil).Or(new_water).selfMask().rename("flag")

    # Connected clusters ≥ 10 pixels, then polygons. 8-connected like indices.label_clusters.
    size = flagged.connectedPixelCount(100, True)
    flagged = flagged.updateMask(size.gte(10))
    vectors = flagged.addBands(new.select("ndvi").subtract(base.select("ndvi")).rename("dndvi")).reduceToVectors(
        geometry=region, scale=10, geometryType="polygon", eightConnected=True,
        labelProperty="flag", reducer=e.Reducer.mean(), maxPixels=1e9, bestEffort=True)
    records = vectors_to_records(vectors.getInfo())

    out = []
    for r in records:
        geom = shape(r["geometry"])
        dndvi = float(r.get("mean") or 0)
        conf = max(0.3, min(0.95, 0.4 + abs(dndvi)))       # bigger vegetation loss → more confident
        rep = geom.representative_point()
        out.append({"source": "s2_change", "geometry": geom, "image_date": scene_day,
                    "confidence": round(conf, 3), "tile_id": tile_id,
                    "cloud_affected": cloud_pct > 30,
                    "metrics": {"delta_ndvi": round(dndvi, 3), "cloud_pct": cloud_pct},
                    "external_id": f"s2|{tile_id}|{scene_day}|{rep.x:.4f}|{rep.y:.4f}"})

    if river_segments:
        out.extend(_turbidity(raw, base_col, river_segments, scene_day, tile_id))
    stats["detections"] = len(out)
    return out, stats


def _turbidity(raw, base_col, segments: list[dict], scene_day: date, tile_id: str) -> list[dict]:
    """Per-segment NDTI against that segment's own 90-day history. Reported by segment."""
    e = ee()
    import numpy as np

    new = _with_indices(_mask(raw.set("system:time_start", e.Date(scene_day.isoformat()).millis())))
    out = []
    for seg in segments:
        g = to_ee_geometry(seg["geometry"])
        val = new.select("ndti").reduceRegion(e.Reducer.mean(), g, 10, maxPixels=1e8).get("ndti").getInfo()
        hist = base_col.map(lambda im: e.Feature(None, im.select("ndti").reduceRegion(
            e.Reducer.mean(), g, 10, maxPixels=1e8))).aggregate_array("ndti").getInfo()
        series = np.array([h for h in hist if h is not None], dtype=float)
        if val is not None and ix.turbidity_jump(series, float(val)):
            out.append({"source": "s2_turbidity", "geometry": seg["geometry"], "image_date": scene_day,
                        "confidence": 0.6, "tile_id": tile_id,
                        "metrics": {"river": seg.get("name"), "ndti": round(val, 3),
                                    "baseline_mean": round(float(series.mean()), 3)},
                        "external_id": f"turb|{tile_id}|{seg.get('name')}|{scene_day}"})
    return out
