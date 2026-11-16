"""Google Earth Engine connection.

Auth for scheduled runs: a Google Cloud service account registered for Earth Engine.
Put its JSON key in the GEE_SERVICE_ACCOUNT_KEY secret (the whole JSON, or a path),
and the Cloud project id in GEE_PROJECT.

Licensing note: Earth Engine is free for non-commercial and research use. An
operational public-interest service may need a commercial or government
licence — confirm with Google before going live. The Copernicus Data Space
(openEO) fallback in copernicus.py avoids that dependency.
"""

from __future__ import annotations

import json
import os
import tempfile

_ready = False


def ee():
    """Return an initialised `ee` module (imported lazily so tests don't need it)."""
    global _ready
    import ee as _ee

    if _ready:
        return _ee
    key = os.environ.get("GEE_SERVICE_ACCOUNT_KEY", "").strip()
    project = os.environ.get("GEE_PROJECT") or None
    if key:
        if key.startswith("{"):
            info = json.loads(key)
            with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
                fh.write(key)
                key_path = fh.name
        else:
            key_path = key
            info = json.loads(open(key_path).read())
        creds = _ee.ServiceAccountCredentials(info["client_email"], key_path)
        _ee.Initialize(creds, project=project)
    else:
        _ee.Initialize(project=project)      # local runs after `earthengine authenticate`
    _ready = True
    return _ee


def to_ee_geometry(geom):
    from shapely.geometry import mapping
    return ee().Geometry(mapping(geom))


def vectors_to_records(fc_info: dict) -> list[dict]:
    """Earth Engine FeatureCollection.getInfo() → list of {'geometry': geojson, **properties}."""
    return [{"geometry": f["geometry"], **(f.get("properties") or {})} for f in fc_info.get("features", [])]
