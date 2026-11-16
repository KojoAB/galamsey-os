"""Fusion (spec 5.6): turn detections from many sources into one alert per site.

Rules
- Detections whose polygons overlap (or touch within a small tolerance) and whose
  image dates are within `window_days` of each other belong to the same alert.
- A new detection that overlaps an existing open alert is attached to it, which
  also marks that alert as persistent (seen again).
- Everything is done in UTM metres so areas and tolerances are real distances.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import geopandas as gpd
from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from .geo import UTM30N, WGS84

TOUCH_TOLERANCE_M = 20      # two clearings 20 m apart are treated as one site


def as_multipolygon(geom: BaseGeometry) -> MultiPolygon:
    if isinstance(geom, MultiPolygon):
        return geom
    if isinstance(geom, Polygon):
        return MultiPolygon([geom])
    polys = [g for g in getattr(geom, "geoms", []) if isinstance(g, Polygon)]
    return MultiPolygon(polys)


@dataclass
class Cluster:
    detection_ids: list = field(default_factory=list)
    sources: set = field(default_factory=set)
    geom_m: BaseGeometry | None = None          # metric geometry
    dates: set = field(default_factory=set)
    max_confidence: float = 0.0
    cloud_affected: bool = False
    existing_alert_id: int | None = None

    @property
    def first_seen(self) -> date:
        return min(self.dates)

    @property
    def last_seen(self) -> date:
        return max(self.dates)

    @property
    def area_ha(self) -> float:
        return self.geom_m.area / 10_000

    @property
    def persistent(self) -> bool:
        return len(self.dates) >= 2 or self.existing_alert_id is not None


def cluster_detections(detections: gpd.GeoDataFrame, existing_alerts: gpd.GeoDataFrame | None = None,
                       window_days: int = 7) -> list[Cluster]:
    """Group detections into sites.

    detections: columns id, source, image_date (date), confidence, cloud_affected (opt), geometry
    existing_alerts: open alerts (id, last_seen, geometry) to attach to instead of creating new ones
    """
    if detections.empty:
        return []
    det = detections.to_crs(UTM30N).reset_index(drop=True)
    det["_buf"] = det.geometry.buffer(TOUCH_TOLERANCE_M / 2)

    # Union-find over overlapping, date-compatible detections.
    parent = list(range(len(det)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    sindex = gpd.GeoSeries(det["_buf"], crs=UTM30N).sindex
    for i, row in det.iterrows():
        for j in sindex.query(row["_buf"], predicate="intersects"):
            if j <= i:
                continue
            if abs((row["image_date"] - det.at[j, "image_date"]).days) <= window_days:
                parent[find(i)] = find(j)

    clusters: dict[int, Cluster] = {}
    for i, row in det.iterrows():
        c = clusters.setdefault(find(i), Cluster())
        c.detection_ids.append(row["id"])
        c.sources.add(row["source"])
        c.dates.add(row["image_date"])
        c.max_confidence = max(c.max_confidence, float(row.get("confidence") or 0))
        c.cloud_affected = c.cloud_affected or bool(row.get("cloud_affected", False))
        c.geom_m = row.geometry if c.geom_m is None else unary_union([c.geom_m, row.geometry])

    out = list(clusters.values())
    if existing_alerts is not None and not existing_alerts.empty:
        ex = existing_alerts.to_crs(UTM30N)
        ex_index = ex.sindex
        for c in out:
            hits = ex_index.query(c.geom_m.buffer(TOUCH_TOLERANCE_M / 2), predicate="intersects")
            if len(hits):
                # Attach to the existing alert with the biggest overlap.
                best = max(hits, key=lambda k: ex.geometry.iloc[k].intersection(c.geom_m.buffer(1)).area)
                c.existing_alert_id = int(ex["id"].iloc[best])
                c.geom_m = unary_union([c.geom_m, ex.geometry.iloc[best]])
                if "first_seen" in ex.columns:
                    c.dates.add(ex["first_seen"].iloc[best])
    return out


def cluster_geometry_wgs84(c: Cluster) -> MultiPolygon:
    g = gpd.GeoSeries([c.geom_m], crs=UTM30N).to_crs(WGS84).iloc[0]
    return as_multipolygon(g.buffer(0))
