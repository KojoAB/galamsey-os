"""Integration tests that need real services. Skipped automatically without credentials.

Run with Earth Engine set up:   GEE_LOCAL_AUTH=1 pytest -m integration
"""

import os
from datetime import date, timedelta

import pytest

from galamsey.validate import KNOWN_SITES, precision

needs_ee = pytest.mark.skipif(not (os.environ.get("GEE_SERVICE_ACCOUNT_KEY") or os.environ.get("GEE_LOCAL_AUTH")),
                              reason="Earth Engine credentials not configured")


def test_precision_ignores_unreviewed():
    assert precision([("1", "confirm"), ("2", "reject"), ("3", "confirm"), ("4", "needs_more_info")]) == \
        {"reviewed": 3, "confirmed": 2, "rejected": 1, "precision": 0.667}


@needs_ee
@pytest.mark.integration
def test_end_to_end_known_mining_site_atewa():
    """End-to-end on a publicly reported galamsey area: the optical detector should
    find at least one change cluster at Atewa over the last 6 months of scenes."""
    from galamsey.detect import sentinel2
    geom = KNOWN_SITES["Atewa Range FR (Eastern)"]
    days = sentinel2.new_scene_dates(geom, date.today() - timedelta(days=180), date.today())
    assert days, "no Sentinel-2 scenes found"
    found = sum(len(sentinel2.detect_tile(geom, d, "e2e-atewa")[0]) for d in days[-8:])
    assert found >= 1
