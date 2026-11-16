"""Before/after image chips for each alert (spec 5.7): Sentinel-2 true colour,
Sentinel-2 false colour (vegetation shows red, bare ground cyan) and Sentinel-1 radar.
Each PNG gets a date stamp and a scale bar, then goes to Supabase Storage (bucket 'chips').
"""

from __future__ import annotations

import io
import logging
import os
from datetime import date, timedelta

import requests
from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger(__name__)

CHIP_PX = 512
CHIP_HALF_M = 1_000      # chips cover 2 km × 2 km around the alert


def annotate(png: bytes, label: str, metres_across: float, bar_m: int = 500) -> bytes:
    """Add a date/label strip and a scale bar. Pure PIL so it's unit-testable."""
    im = Image.open(io.BytesIO(png)).convert("RGB")
    d = ImageDraw.Draw(im)
    font = ImageFont.load_default(size=max(12, im.width // 32))
    pad = im.width // 40
    tw = d.textlength(label, font=font)
    d.rectangle([0, 0, tw + 2 * pad, font.size + 2 * pad], fill=(10, 15, 13))
    d.text((pad, pad), label, fill=(255, 255, 255), font=font)

    bar_px = round(im.width * bar_m / metres_across)
    x0, y0 = pad, im.height - pad - 6
    d.rectangle([x0 - 4, y0 - font.size - 10, x0 + bar_px + 4, y0 + 10], fill=(10, 15, 13))
    d.rectangle([x0, y0, x0 + bar_px, y0 + 4], fill=(255, 255, 255))
    d.text((x0, y0 - font.size - 6), f"{bar_m} m", fill=(255, 255, 255), font=font)
    out = io.BytesIO()
    im.save(out, "PNG", optimize=True)
    return out.getvalue()


def _thumb(image, region, vis: dict) -> bytes:
    url = image.getThumbURL({**vis, "region": region, "dimensions": CHIP_PX, "format": "png"})
    resp = requests.get(url, timeout=120)
    resp.raise_for_status()
    return resp.content


def render_chips(centroid_lon: float, centroid_lat: float, first_seen: date, last_seen: date) -> dict[str, bytes]:
    """Return {name: png} for before (30–90 days before first_seen) and after (last 30 days to last_seen)."""
    from .detect.earthengine import ee
    from .detect.sentinel2 import _mask, collection

    e = ee()
    pt = e.Geometry.Point([centroid_lon, centroid_lat])
    region = pt.buffer(CHIP_HALF_M).bounds()
    windows = {"before": (first_seen - timedelta(days=90), first_seen - timedelta(days=5)),
               "after": (last_seen - timedelta(days=30), last_seen + timedelta(days=1))}
    out = {}
    for when, (start, end) in windows.items():
        s2 = collection(region, start, end).map(_mask).median()
        s1 = (e.ImageCollection("COPERNICUS/S1_GRD").filterBounds(region).filterDate(start.isoformat(), end.isoformat())
              .filter(e.Filter.eq("instrumentMode", "IW")).select("VV").median())
        label_dates = f"{start:%d %b %Y} – {end:%d %b %Y}"
        for name, img, vis in (
            ("s2_true", s2, {"bands": ["B4", "B3", "B2"], "min": 0, "max": 0.3}),
            ("s2_false", s2, {"bands": ["B8", "B4", "B3"], "min": 0, "max": 0.45}),
            ("s1_vv", s1, {"min": -22, "max": 0}),
        ):
            try:
                png = _thumb(img, region, vis)
                pretty = {"s2_true": "Sentinel-2 true colour", "s2_false": "Sentinel-2 false colour",
                          "s1_vv": "Sentinel-1 radar VV"}[name]
                out[f"{name}_{when}"] = annotate(png, f"{pretty} · {when} · {label_dates}", 2 * CHIP_HALF_M)
            except Exception as exc:          # one missing chip must not stop the others
                log.warning("chip %s_%s failed: %s", name, when, exc)
    return out


def upload(bucket: str, path: str, data: bytes, content_type: str = "image/png") -> str:
    """Upload to Supabase Storage with the service-role key. Returns the object path."""
    url, key = os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    resp = requests.post(f"{url}/storage/v1/object/{bucket}/{path}", data=data, timeout=60,
                         headers={"Authorization": f"Bearer {key}", "Content-Type": content_type, "x-upsert": "true"})
    resp.raise_for_status()
    return path
