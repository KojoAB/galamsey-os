"""Source chain: try the official agency source first, then open fallbacks.

Every reference layer is described by a list of Sources in priority order. The
loader takes the first one that is configured and returns data. Whatever wins is
recorded on each row (source, retrieved_at, licence, verified), so the map and
admin panel always show where a layer came from and how fresh it is.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

import geopandas as gpd
import requests

from ..config import settings
from ..geo import clean

log = logging.getLogger(__name__)


class SourceUnavailable(Exception):
    """Raised when a source is not configured or not reachable. The chain moves on."""


@dataclass
class LayerData:
    gdf: gpd.GeoDataFrame          # must contain 'name' and 'geometry'
    source: str
    licence: str
    verified: bool
    retrieved_at: datetime


@dataclass
class Source:
    key: str                       # e.g. 'forestry_commission:reserves'
    licence: str
    verified: bool
    fetch: Callable[[], gpd.GeoDataFrame]

    def load(self) -> LayerData:
        raw = self.fetch()
        licence = raw.attrs.get("licence", self.licence)   # a fetcher may report a more exact licence
        gdf = clean(raw)
        if gdf.empty:
            raise SourceUnavailable(f"{self.key} returned no features")
        if "name" not in gdf.columns:
            gdf["name"] = None
        return LayerData(gdf=gdf, source=self.key, licence=licence,
                         verified=self.verified, retrieved_at=datetime.now(timezone.utc))


def first_available(sources: list[Source]) -> tuple[LayerData, list[str]]:
    """Return data from the first working source plus a log of what was skipped and why."""
    skipped: list[str] = []
    for src in sources:
        try:
            data = src.load()
            log.info("Using %s (%d features)", src.key, len(data.gdf))
            return data, skipped
        except SourceUnavailable as exc:
            skipped.append(f"{src.key}: {exc}")
        except (requests.RequestException, OSError, ValueError) as exc:
            skipped.append(f"{src.key}: {type(exc).__name__}: {exc}")
        log.warning("Skipped %s", skipped[-1])
    raise SourceUnavailable("No source available:\n  " + "\n  ".join(skipped))


# ---------------------------------------------------------------------------
# Generic fetchers
# ---------------------------------------------------------------------------

def from_file_or_url(location: str | None, name_field: str | None = None) -> Callable[[], gpd.GeoDataFrame]:
    """Read GeoJSON / zipped Shapefile / GeoPackage from a path or URL (agency exports)."""
    def fetch() -> gpd.GeoDataFrame:
        if not location:
            raise SourceUnavailable("not configured")
        gdf = gpd.read_file(location)
        if name_field and name_field in gdf.columns:
            gdf = gdf.rename(columns={name_field: "name"})
        elif "name" not in gdf.columns:
            for guess in ("NAME", "Name", "shapeName", "RESERVE", "Reserve_Na", "RIVER_NAME", "District", "REGION"):
                if guess in gdf.columns:
                    gdf = gdf.rename(columns={guess: "name"})
                    break
        return gdf
    return fetch


def http_get_json(url: str, **params) -> dict:
    resp = requests.get(url, params=params or None, timeout=settings.http_timeout_s,
                        headers={"User-Agent": "GalamseyWatch/0.1 (reference-data loader)"})
    resp.raise_for_status()
    return resp.json()


def overpass(query: str) -> dict:
    """Run an Overpass API query (OpenStreetMap). Free, no key; be polite with volume."""
    resp = requests.post(settings.overpass_url, data={"data": query}, timeout=settings.http_timeout_s,
                         headers={"User-Agent": "GalamseyWatch/0.1 (reference-data loader)"})
    resp.raise_for_status()
    return resp.json()


GHANA_AREA = 'area["ISO3166-1"="GH"][admin_level=2]->.gh;'
OSM_LICENCE = "ODbL 1.0 — © OpenStreetMap contributors"
