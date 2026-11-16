"""Spectral indices and the change rule, in plain numpy.

These functions are the reference definition of our optical detector. The Earth
Engine version (sentinel2.py) applies the same formulas server-side; the Planet
module (planet.py) applies them to 4-band PlanetScope imagery; the unit tests check
them on small synthetic images.

What each index means, for non-specialists:
- NDVI  (near-infrared vs red): high = healthy vegetation. A big drop = cleared land.
- BSI   (bare soil index): high = exposed earth, pits and spoil heaps.
- MNDWI (green vs shortwave-infrared): high = open water. New water on former forest
        = mining ponds.
- NDTI  (red vs green) on river pixels: higher = muddier water (sediment from mining).
"""

from __future__ import annotations

import numpy as np

# Default thresholds (spec 5.3). Editable via settings later if needed.
NDVI_DROP = 0.25
BSI_RISE = 0.05
WATER_THRESHOLD = 0.0      # MNDWI above this = water
VEGETATED_NDVI = 0.5       # baseline NDVI above this = vegetated land


def _ratio(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    a = a.astype("float64")
    b = b.astype("float64")
    with np.errstate(divide="ignore", invalid="ignore"):
        out = (a - b) / (a + b)
    return np.where(np.isfinite(out), out, np.nan)


def ndvi(nir, red):
    return _ratio(nir, red)


def mndwi(green, swir1):
    return _ratio(green, swir1)


def ndwi(green, nir):
    """McFeeters NDWI — used when there is no SWIR band (PlanetScope)."""
    return _ratio(green, nir)


def ndti(red, green):
    return _ratio(red, green)


def bsi(blue, red, nir, swir1):
    blue, red, nir, swir1 = (x.astype("float64") for x in (blue, red, nir, swir1))
    with np.errstate(divide="ignore", invalid="ignore"):
        out = ((swir1 + red) - (nir + blue)) / ((swir1 + red) + (nir + blue))
    return np.where(np.isfinite(out), out, np.nan)


def change_mask(ndvi_new, ndvi_base, bsi_new=None, bsi_base=None, water_new=None, water_base=None,
                ndvi_drop=NDVI_DROP, bsi_rise=BSI_RISE) -> np.ndarray:
    """True where land looks newly mined.

    Rule A: NDVI fell by more than `ndvi_drop` AND bare soil rose (if BSI is available).
    Rule B: water appeared where the baseline was vegetated land.
    NaN (cloud/no data) is never flagged.
    """
    drop = (ndvi_base - ndvi_new) > ndvi_drop
    if bsi_new is not None and bsi_base is not None:
        drop &= (bsi_new - bsi_base) > bsi_rise
    rule = drop
    if water_new is not None and water_base is not None:
        new_water = (water_new > WATER_THRESHOLD) & (water_base <= WATER_THRESHOLD) & (ndvi_base > VEGETATED_NDVI)
        rule = rule | new_water
    valid = np.isfinite(ndvi_new) & np.isfinite(ndvi_base)
    return np.where(valid, rule, False)


def turbidity_jump(series_baseline: np.ndarray, value_new: float, z: float = 3.0, min_rise: float = 0.05) -> bool:
    """River segment turbidity alert: new mean NDTI is far above its own 90-day history."""
    hist = series_baseline[np.isfinite(series_baseline)]
    if hist.size < 3 or not np.isfinite(value_new):
        return False
    mean, sd = hist.mean(), max(hist.std(), 0.01)
    return (value_new - mean) / sd >= z and (value_new - mean) >= min_rise


def log_ratio_db(new_db: np.ndarray, base_db: np.ndarray) -> np.ndarray:
    """Radar change in decibels. Earth Engine's Sentinel-1 GRD is already in dB, so the
    log-ratio is a subtraction. Strongly negative = surface became smooth (water, bare mud)."""
    return new_db - base_db


def radar_change_mask(vv_new, vv_base, vh_new, vh_base, drop_db=-3.0, vegetated_vh_db=-18.0):
    """Flag strong backscatter drops (new water / smooth bare ground) on land that was vegetated."""
    vv = log_ratio_db(vv_new, vv_base)
    vh = log_ratio_db(vh_new, vh_base)
    vegetated = vh_base > vegetated_vh_db
    return (vv < drop_db) & (vh < drop_db) & vegetated


def label_clusters(mask: np.ndarray, min_pixels: int = 10) -> np.ndarray:
    """Connected groups of flagged pixels (8-neighbour); groups smaller than min_pixels removed.
    10 pixels at 10 m ≈ 0.1 ha."""
    from skimage.measure import label

    lab = label(mask.astype(bool), connectivity=2)
    if lab.max() == 0:
        return lab
    counts = np.bincount(lab.ravel())
    small = np.where(counts < min_pixels)[0]
    lab[np.isin(lab, small)] = 0
    return label(lab > 0, connectivity=2)   # renumber the survivors 1..n
