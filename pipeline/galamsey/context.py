"""Spatial context for an alert: which region/district, forest reserve, river distance,
concession. Reference layers are loaded once per run and kept in UTM metres."""

from __future__ import annotations

from dataclasses import dataclass

import geopandas as gpd
from shapely.geometry.base import BaseGeometry

from .geo import UTM30N


@dataclass
class Context:
    region: str | None = None
    district: str | None = None
    in_forest_reserve: bool = False
    forest_reserve_name: str | None = None
    river_distance_m: int | None = None
    river_name: str | None = None
    in_concession: bool | None = None       # None when no concession layer is loaded


class ReferenceIndex:
    def __init__(self, layers: dict[str, gpd.GeoDataFrame]):
        self.layers = {k: v.to_crs(UTM30N) for k, v in layers.items() if v is not None and not v.empty}

    def _covering_name(self, layer: str, geom_m: BaseGeometry) -> str | None:
        gdf = self.layers.get(layer)
        if gdf is None:
            return None
        hits = gdf.iloc[gdf.sindex.query(geom_m, predicate="intersects")]
        if hits.empty:
            return None
        overlap = hits.geometry.intersection(geom_m).area
        return hits.loc[overlap.idxmax(), "name"]

    def context_for(self, geom_m: BaseGeometry) -> Context:
        ctx = Context()
        ctx.region = self._covering_name("admin1", geom_m)
        ctx.district = self._covering_name("admin2", geom_m)

        reserve = self._covering_name("forest_reserve", geom_m)
        ctx.in_forest_reserve = reserve is not None
        ctx.forest_reserve_name = reserve

        rivers = self.layers.get("river")
        if rivers is not None:
            # Only search rivers within 5 km — beyond that the distance doesn't affect scoring.
            near = rivers.iloc[rivers.sindex.query(geom_m.buffer(5_000), predicate="intersects")]
            if not near.empty:
                d = near.geometry.distance(geom_m)
                ctx.river_distance_m = int(round(d.min()))
                ctx.river_name = near.loc[d.idxmin(), "name"]

        conc = self.layers.get("concession")
        if conc is not None:
            # "Inside" = at least half the clearing lies in a licensed area.
            hits = conc.iloc[conc.sindex.query(geom_m, predicate="intersects")]
            inside = hits.geometry.intersection(geom_m).area.sum() if not hits.empty else 0.0
            ctx.in_concession = inside >= 0.5 * geom_m.area
        return ctx
