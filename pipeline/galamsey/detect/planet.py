"""Optional paid module (M12) — PlanetScope daily imagery for the mining-belt priority
zones only. OFF by default.

Two switches must both be on before anything is ordered:
  1. env PLANET_ENABLED=1 and PLANET_API_KEY set
  2. settings.planet_enabled = true (set by an admin in the dashboard)

What it does
- create_subscription(): one Planet Subscriptions API subscription per priority zone
  (AOI = the zone polygon, item type PSScene, 4-band surface reflectance), delivered to
  Planet's hosted storage. Creating a subscription COSTS MONEY — it is a separate CLI
  step, never automatic.
- detect(): applies the same change rule as Sentinel-2 (indices.py) adapted to 4-band
  3 m imagery. PlanetScope has no shortwave-infrared band, so bare soil is approximated
  by "NDVI dropped and brightness rose", and water by green/NIR NDWI. Detections feed the
  normal fusion step with source 'planet_change'.
- Every order is recorded in paid_spend so the admin panel shows monthly cost.
"""

from __future__ import annotations

import os

import numpy as np
import requests

from . import indices as ix

API = "https://api.planet.com/subscriptions/v1"
PX_M = 3


def enabled(conn=None) -> bool:
    if os.environ.get("PLANET_ENABLED") != "1" or not os.environ.get("PLANET_API_KEY"):
        return False
    if conn is None:
        return False
    with conn.cursor() as cur:
        cur.execute("select value from settings where key = 'planet_enabled'")
        row = cur.fetchone()
    return bool(row and row[0] is True)


def subscription_request(zone_name: str, geometry: dict, start_iso: str) -> dict:
    """Body for Planet's Subscriptions API (kept separate so it can be reviewed/tested)."""
    return {
        "name": f"galamsey-watch-{zone_name}".lower().replace(" ", "-")[:60],
        "source": {"parameters": {"item_types": ["PSScene"], "asset_types": ["ortho_analytic_4b_sr"],
                                  "geometry": geometry, "start_time": start_iso,
                                  "filter": {"type": "RangeFilter", "field_name": "cloud_cover", "config": {"lte": 0.6}}}},
        "tools": [{"type": "clip", "parameters": {"aoi": geometry}}],
        "hosting": {"type": "sentinel_hub"},
    }


def create_subscription(conn, zone_name: str, geometry: dict, start_iso: str, estimated_cost_usd: float) -> str:
    if not enabled(conn):
        raise RuntimeError("Planet module is disabled (PLANET_ENABLED and settings.planet_enabled must both be on)")
    r = requests.post(API, json=subscription_request(zone_name, geometry, start_iso), timeout=60,
                      auth=(os.environ["PLANET_API_KEY"], ""))
    r.raise_for_status()
    sub_id = r.json()["id"]
    with conn.cursor() as cur:
        cur.execute("insert into paid_spend (provider, description, amount_usd, reference) values ('planet', %s, %s, %s)",
                    (f"Subscription for {zone_name}", estimated_cost_usd, sub_id))
    conn.commit()
    return sub_id


def change_mask_4band(new: np.ndarray, base: np.ndarray) -> np.ndarray:
    """new/base: (4, H, W) surface reflectance 0–1 in band order blue, green, red, NIR."""
    b, g, r, n = new
    B, G, R, N = base
    nd_new, nd_base = ix.ndvi(n, r), ix.ndvi(N, R)
    brightness_rise = (b + g + r) / 3 - (B + G + R) / 3 > 0.03
    cleared = ((nd_base - nd_new) > ix.NDVI_DROP) & brightness_rise
    water_new, water_base = ix.ndwi(g, n), ix.ndwi(G, N)
    new_water = (water_new > 0) & (water_base <= 0) & (nd_base > ix.VEGETATED_NDVI)
    valid = np.isfinite(nd_new) & np.isfinite(nd_base)
    return np.where(valid, cleared | new_water, False)


def min_pixels_for(area_ha: float = 0.1) -> int:
    """0.1 ha at 3 m pixels = 112 pixels."""
    return int(np.ceil(area_ha * 10_000 / (PX_M * PX_M)))
