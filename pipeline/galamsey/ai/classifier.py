"""Source D — AI mine classifier (spec 5.5).

Adapted from Amazon Mining Watch's gold-mine detector (Earth Genome,
github.com/earthrise-media/mining-detector, MIT licence). We reuse their
production model and input recipe exactly:

- Input: a 48 × 48 pixel patch (≈ 480 m square) of Sentinel-2 L1C, all 13 bands in the
  order B1 B2 B3 B4 B5 B6 B7 B8A B8 B9 B10 B11 B12.
- Pixels are a cloud-masked median composite (Cloud Score+ cs_cdf ≥ 0.6) over a
  multi-month window, scaled by /10000 and clipped to 0–1.
- Model: the August 2026 production ensemble `48px_v4.10b-18d-20g-21a-22bc-ensemble.h5`
  (six CNNs, averaged). Their tuned threshold is 0.43; isolated patches need 0.75.

Until we have ≥ 300 confirmed Ghana mines (see retrain.py), the classifier is a
*secondary* signal only: its probability adds points to the score, but it never
creates an alert on its own.

Requires the optional extra: pip install -r requirements-ai.txt (TensorFlow).
"""

from __future__ import annotations

import logging
import os
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import requests

log = logging.getLogger(__name__)

BANDS = ["B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8A", "B8", "B9", "B10", "B11", "B12"]
PATCH = 48
THRESHOLD = 0.43
ISOLATED_THRESHOLD = 0.75
MODEL_NAME = "48px_v4.10b-18d-20g-21a-22bc-ensemble.h5"
MODEL_URL = f"https://github.com/earthrise-media/mining-detector/raw/main/models/{MODEL_NAME}"
CACHE = Path(os.environ.get("MODEL_CACHE_DIR", Path.home() / ".cache" / "galamsey-watch"))


def model_path() -> Path:
    """Ghana fine-tuned model if one exists, else the downloaded Amazon Mining Watch model."""
    ghana = CACHE / "ghana_finetuned.h5"
    if ghana.exists():
        return ghana
    path = CACHE / MODEL_NAME
    if not path.exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        log.info("Downloading %s", MODEL_URL)
        with requests.get(MODEL_URL, stream=True, timeout=300) as r:
            r.raise_for_status()
            with open(path, "wb") as fh:
                for chunk in r.iter_content(1 << 20):
                    fh.write(chunk)
    return path


_model = None


def load_model():
    global _model
    if _model is None:
        import tensorflow as tf
        _model = tf.keras.models.load_model(model_path(), compile=False)
    return _model


def normalise(patch: np.ndarray) -> np.ndarray:
    """Raw S2 L1C digital numbers (H, W, 13) → model input, as in the original pipeline."""
    return np.clip(patch.astype("float32") / 10000.0, 0, 1)


def fetch_patch(lon: float, lat: float, end: date, months: int = 4) -> np.ndarray:
    """48×48×13 median composite centred on a point, straight from Earth Engine."""
    from ..detect.earthengine import ee

    e = ee()
    start = end - timedelta(days=30 * months)
    comp = (e.ImageCollection("COPERNICUS/S2_HARMONIZED").filterDate(start.isoformat(), end.isoformat())
            .linkCollection(e.ImageCollection("GOOGLE/CLOUD_SCORE_PLUS/V1/S2_HARMONIZED"), ["cs_cdf"])
            .map(lambda im: im.updateMask(im.select("cs_cdf").gte(0.6))).median().select(BANDS))
    # Pixel grid in UTM 30N at 10 m, centred on the point.
    from pyproj import Transformer
    x, y = Transformer.from_crs("EPSG:4326", "EPSG:32630", always_xy=True).transform(lon, lat)
    x0, y0 = x - PATCH * 5, y + PATCH * 5
    arr = e.data.computePixels({
        "expression": comp.unmask(0),
        "fileFormat": "NUMPY_NDARRAY",
        "grid": {"dimensions": {"width": PATCH, "height": PATCH},
                 "affineTransform": {"scaleX": 10, "shearX": 0, "translateX": x0,
                                     "shearY": 0, "scaleY": -10, "translateY": y0},
                 "crsCode": "EPSG:32630"},
    })
    return np.stack([arr[b] for b in BANDS], axis=-1)


def predict(patches: np.ndarray) -> np.ndarray:
    """(N, 48, 48, 13) raw → (N,) mine probability."""
    x = normalise(patches)
    return load_model().predict(x, verbose=0).reshape(-1)


def score_alert_point(lon: float, lat: float, end: date) -> float | None:
    try:
        return float(predict(fetch_patch(lon, lat, end)[None, ...])[0])
    except ImportError:
        log.info("TensorFlow not installed; AI classifier skipped")
        return None
    except Exception as exc:              # never block the daily run on the secondary signal
        log.warning("AI classifier failed at %.5f,%.5f: %s", lon, lat, exc)
        return None
