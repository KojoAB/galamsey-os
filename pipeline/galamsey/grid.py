"""The national processing grid: 10 km × 10 km tiles covering Ghana.

Each tile is a square in UTM 30N metres. Its id encodes its south-west corner in
kilometres, e.g. 'E0610_N0700' starts at easting 610 km, northing 700 km. Ids are
therefore stable: rebuilding the grid produces the same ids, so re-runs are safe.

Ghana (~238,500 km²) needs roughly 2,600 tiles once edge tiles are included.
"""

from __future__ import annotations

import math

import geopandas as gpd
from shapely.geometry import box

from .geo import UTM30N, WGS84

TILE_SIZE_M = 10_000


def tile_id(easting_m: float, northing_m: float) -> str:
    return f"E{int(easting_m // 1000):04d}_N{int(northing_m // 1000):04d}"


def build_grid(boundary: gpd.GeoDataFrame, size_m: int = TILE_SIZE_M) -> gpd.GeoDataFrame:
    """Return every grid square that touches the boundary, in WGS84.

    Squares are kept whole (not clipped to the border) so every tile has the same
    area, which keeps per-tile statistics comparable.
    """
    country = boundary.to_crs(UTM30N).union_all()
    minx, miny, maxx, maxy = country.bounds

    # Snap the grid origin to a multiple of the tile size so ids are stable.
    x0 = math.floor(minx / size_m) * size_m
    y0 = math.floor(miny / size_m) * size_m

    squares, ids = [], []
    y = y0
    while y < maxy:
        x = x0
        while x < maxx:
            squares.append(box(x, y, x + size_m, y + size_m))
            ids.append(tile_id(x, y))
            x += size_m
        y += size_m

    grid = gpd.GeoDataFrame({"id": ids}, geometry=squares, crs=UTM30N)
    # Fast spatial-index filter, then exact test.
    grid = grid[grid.intersects(country)].reset_index(drop=True)
    grid["priority"] = "normal"
    return grid.to_crs(WGS84)


def flag_priority(grid: gpd.GeoDataFrame, priority_zones: gpd.GeoDataFrame | None) -> gpd.GeoDataFrame:
    """Mark tiles touching a mining-belt priority zone as 'high' (processed first, every day)."""
    grid = grid.copy()
    if priority_zones is None or priority_zones.empty:
        grid["priority"] = "normal"
        return grid
    zones = priority_zones.to_crs(grid.crs).union_all()
    grid["priority"] = grid.intersects(zones).map({True: "high", False: "normal"})
    return grid
