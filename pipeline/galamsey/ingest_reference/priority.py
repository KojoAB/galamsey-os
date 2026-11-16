"""Mining-belt priority zones built from a list of hotspot district names."""

from __future__ import annotations

import re

import geopandas as gpd

_NOISE = re.compile(r"\b(municipal|metropolitan|district|assembly|area)\b", re.I)


def normalise_name(name: str) -> str:
    """'Prestea Huni-Valley Municipal' and 'prestea-huni valley' both become 'prestea huni valley'."""
    name = _NOISE.sub(" ", name or "")
    name = re.sub(r"[^a-z0-9]+", " ", name.lower())
    return " ".join(name.split())


def match_districts(districts: gpd.GeoDataFrame, wanted: dict[str, list[str]]) -> tuple[gpd.GeoDataFrame, list[str]]:
    """Select districts whose names match the wanted list. Returns (zones, names not found)."""
    lookup = {normalise_name(n): i for i, n in districts["name"].items()}
    picked, missing = [], []
    for region, names in wanted.items():
        for raw in names:
            key = normalise_name(raw)
            if key in lookup:
                picked.append((lookup[key], region))
            else:
                missing.append(f"{raw} ({region})")
    if not picked:
        return districts.iloc[0:0].copy(), missing
    idx, regions = zip(*picked)
    zones = districts.loc[list(idx)].copy()
    zones["region_hint"] = list(regions)
    return zones.drop_duplicates(subset="name"), missing
