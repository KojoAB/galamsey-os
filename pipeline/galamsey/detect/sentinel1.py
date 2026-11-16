"""Source C — Sentinel-1 radar change detection (spec 5.4). Radar sees through cloud,
so this is the main detector in the rainy season; optical confirms.

For each new Sentinel-1 GRD scene over a tile:
- Use VV and VH, and compare only against images from the SAME orbit direction and
  relative orbit (otherwise viewing geometry differences look like change).
- Baseline = 60-day median. Speckle is reduced with a 50 m focal median.
- Earth Engine GRD is already in decibels, so the log-ratio is new − baseline.
- Flag strong drops in both bands (new water / smooth bare ground) on land that was vegetated.
"""

from __future__ import annotations

from datetime import date, timedelta

from shapely.geometry import shape

from .earthengine import ee, to_ee_geometry, vectors_to_records

S1 = "COPERNICUS/S1_GRD"
DROP_DB = -3.0
VEGETATED_VH_DB = -18.0


def _col(region, start: date, end: date):
    e = ee()
    return (e.ImageCollection(S1).filterBounds(region).filterDate(start.isoformat(), end.isoformat())
            .filter(e.Filter.eq("instrumentMode", "IW"))
            .filter(e.Filter.listContains("transmitterReceiverPolarisation", "VV"))
            .filter(e.Filter.listContains("transmitterReceiverPolarisation", "VH")))


def _despeckle(img):
    return img.focal_median(50, "circle", "meters").copyProperties(img, img.propertyNames())


def new_scenes(tile_geom, since: date, until: date) -> list[dict]:
    col = _col(to_ee_geometry(tile_geom), since, until + timedelta(days=1))
    info = col.reduceColumns(ee().Reducer.toList(3), ["system:time_start", "orbitProperties_pass",
                                                     "relativeOrbitNumber_start"]).get("list").getInfo()
    seen, out = set(), []
    for millis, direction, rel in info:
        key = (date.fromtimestamp(millis / 1000), direction, int(rel))
        if key not in seen:
            seen.add(key)
            out.append({"date": key[0], "pass": direction, "relative_orbit": key[2]})
    return out


def detect_tile(tile_geom, scene: dict, tile_id: str) -> tuple[list[dict], dict]:
    e = ee()
    region = to_ee_geometry(tile_geom)
    same_geometry = (e.Filter.eq("orbitProperties_pass", scene["pass"]),
                     e.Filter.eq("relativeOrbitNumber_start", scene["relative_orbit"]))
    d = scene["date"]
    new = _despeckle(_col(region, d, d + timedelta(days=1)).filter(e.Filter.And(*same_geometry)).mosaic())
    base = _despeckle(_col(region, d - timedelta(days=60), d).filter(e.Filter.And(*same_geometry)).median())

    ratio = new.select(["VV", "VH"]).subtract(base.select(["VV", "VH"]))        # dB difference = log-ratio
    flagged = (ratio.select("VV").lt(DROP_DB).And(ratio.select("VH").lt(DROP_DB))
               .And(base.select("VH").gt(VEGETATED_VH_DB))).selfMask().rename("flag")
    flagged = flagged.updateMask(flagged.connectedPixelCount(100, True).gte(10)).clip(region)

    vectors = flagged.addBands(ratio.select("VV").rename("dvv")).reduceToVectors(
        geometry=region, scale=10, geometryType="polygon", eightConnected=True,
        labelProperty="flag", reducer=e.Reducer.mean(), maxPixels=1e9, bestEffort=True)
    out = []
    for r in vectors_to_records(vectors.getInfo()):
        geom = shape(r["geometry"])
        dvv = float(r.get("mean") or 0)
        conf = max(0.3, min(0.9, 0.3 + abs(dvv) / 10))
        rep = geom.representative_point()
        out.append({"source": "s1_change", "geometry": geom, "image_date": d, "confidence": round(conf, 3),
                    "tile_id": tile_id, "metrics": {"delta_vv_db": round(dvv, 2), "orbit": scene["pass"],
                                                    "relative_orbit": scene["relative_orbit"]},
                    "external_id": f"s1|{tile_id}|{d}|{rep.x:.4f}|{rep.y:.4f}"})
    return out, {"tile_id": tile_id, "date": d.isoformat(), "detections": len(out)}
