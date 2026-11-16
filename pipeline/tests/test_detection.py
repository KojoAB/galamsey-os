"""Tests for indices, scoring, dedup/fusion, GFW pixel merging, templates, notify, connectors."""

import io
from datetime import date

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from PIL import Image
from shapely.geometry import box

from galamsey import notify as nt
from galamsey.chips import annotate
from galamsey.connectors import agencies
from galamsey.detect import indices as ix
from galamsey.detect import planet
from galamsey.detect.gfw import DATASETS, pixels_to_clearings
from galamsey.fusion import cluster_detections
from galamsey.ai.retrain import f05, split_by_region
from galamsey.notify import templates as tpl
from galamsey.scoring import AlertFacts, apply_daily_cap, independent_families, score_alert

# --- indices ----------------------------------------------------------------

def test_ndvi_and_water_indices_known_values():
    assert ix.ndvi(np.array([0.5]), np.array([0.1]))[0] == pytest.approx(0.6667, abs=1e-3)
    assert ix.mndwi(np.array([0.3]), np.array([0.1]))[0] == pytest.approx(0.5)
    assert np.isnan(ix.ndvi(np.array([0.0]), np.array([0.0]))[0])          # no divide-by-zero crash


def test_change_mask_flags_clearing_and_new_water_only():
    shape = (5, 5)
    forest = dict(nir=np.full(shape, 0.45), red=np.full(shape, 0.04), green=np.full(shape, 0.07),
                  blue=np.full(shape, 0.03), swir=np.full(shape, 0.15))
    new = {k: v.copy() for k, v in forest.items()}
    new["nir"][0, 0], new["red"][0, 0], new["swir"][0, 0] = 0.25, 0.20, 0.30     # bare soil
    new["green"][4, 4], new["nir"][4, 4], new["swir"][4, 4] = 0.12, 0.05, 0.03   # pond
    new["red"][2, 2] = np.nan                                                    # cloud

    def idx(b):
        return (ix.ndvi(b["nir"], b["red"]), ix.bsi(b["blue"], b["red"], b["nir"], b["swir"]),
                ix.mndwi(b["green"], b["swir"]))
    n1, s1, w1 = idx(new)
    n0, s0, w0 = idx(forest)
    m = ix.change_mask(n1, n0, s1, s0, w1, w0)
    assert m[0, 0] and m[4, 4]
    assert not m[2, 2] and m.sum() == 2


def test_label_clusters_drops_groups_under_10_pixels():
    mask = np.zeros((20, 20), bool)
    mask[0:4, 0:4] = True        # 16 px → kept
    mask[10:12, 10:13] = True    # 6 px → removed
    lab = ix.label_clusters(mask, 10)
    assert lab.max() == 1 and (lab > 0).sum() == 16


def test_turbidity_jump_needs_a_sharp_rise_against_own_history():
    hist = np.array([0.02, 0.03, 0.025, 0.028, 0.03])
    assert ix.turbidity_jump(hist, 0.15)
    assert not ix.turbidity_jump(hist, 0.04)
    assert not ix.turbidity_jump(np.array([0.02]), 0.5)            # not enough history


def test_radar_mask_needs_drop_in_both_bands_on_vegetated_land():
    vv_b, vh_b = np.array([-7.0, -7.0, -7.0]), np.array([-13.0, -13.0, -22.0])
    vv_n, vh_n = np.array([-15.0, -8.0, -15.0]), np.array([-20.0, -20.0, -28.0])
    assert ix.radar_change_mask(vv_n, vv_b, vh_n, vh_b).tolist() == [True, False, False]


def test_planet_4band_rule_and_pixel_threshold():
    base = np.stack([np.full((3, 3), v) for v in (0.03, 0.06, 0.04, 0.45)])
    new = base.copy()
    new[:, 1, 1] = (0.10, 0.12, 0.15, 0.20)
    assert planet.change_mask_4band(new, base).sum() == 1
    assert planet.min_pixels_for(0.1) == 112
    assert planet.enabled(None) is False                             # off by default


# --- scoring ----------------------------------------------------------------

def test_two_independent_sources_in_reserve_make_red():
    s = score_alert(AlertFacts(sources=["gfw_radd", "s2_change"], area_ha=2.4, max_confidence=0.8,
                               ai_probability=0.9, persistent=True, in_forest_reserve=True,
                               river_distance_m=80, in_concession=False))
    assert s.level == "red" and s.score >= 75
    assert s.breakdown["forest_reserve_or_river"] == 20 and "sources_agree" in s.breakdown


def test_same_satellite_twice_is_one_family():
    assert independent_families(["gfw_glad_s2", "s2_change"]) == {"sentinel2"}
    assert independent_families(["gfw_radd", "s2_change", "ai_classifier"]) == {"sentinel1", "sentinel2"}


def test_high_score_outside_sensitive_zone_is_amber_not_red():
    s = score_alert(AlertFacts(sources=["gfw_radd", "s2_change", "gfw_glad_l"], area_ha=6, max_confidence=0.9,
                               ai_probability=0.95, persistent=True, in_concession=False))
    assert s.score >= 75 and s.level == "amber"


def test_inside_concession_is_penalised_but_still_logged():
    s = score_alert(AlertFacts(sources=["gfw_radd", "s2_change"], area_ha=2, max_confidence=0.8,
                               persistent=True, in_concession=True))
    assert s.breakdown["inside_concession"] == -30 and s.level in ("watch", None)
    assert s.level != "amber"


def test_single_low_confidence_cloudy_detection_is_not_an_alert():
    s = score_alert(AlertFacts(sources=["s1_change"], area_ha=0.15, max_confidence=0.3, cloud_affected=True))
    assert s.level is None and s.breakdown["low_confidence"] == -10


def test_thresholds_and_weights_are_editable():
    f = AlertFacts(sources=["s2_change"], area_ha=1, max_confidence=0.8, in_concession=False)
    assert score_alert(f).level == "watch"
    assert score_alert(f, thresholds={"amber": 35}).level == "amber"
    assert score_alert(f, weights={"base": 60}).score == 80


def test_daily_cap_keeps_top_scores_per_region():
    alerts = [{"id": i, "region": "Eastern", "level": "red", "score": 60 + i} for i in range(5)] + \
             [{"id": 9, "region": "Ashanti", "level": "amber", "score": 55},
              {"id": 10, "region": "Ashanti", "level": "watch", "score": 40}]
    out = {a["id"]: a["digest_only"] for a in apply_daily_cap(alerts, cap=2)}
    assert [i for i in range(5) if not out[i]] == [3, 4]
    assert out[9] is False and out[10] is True


# --- fusion / dedup ---------------------------------------------------------

def _det(rows):
    return gpd.GeoDataFrame(pd.DataFrame(rows), geometry="geometry", crs="EPSG:4326")


def test_overlapping_detections_within_7_days_merge_into_one_alert():
    a = box(-0.600, 6.200, -0.595, 6.205)
    b = box(-0.597, 6.202, -0.592, 6.207)          # overlaps a
    far = box(-1.0, 7.0, -0.995, 7.005)
    det = _det([
        {"id": 1, "source": "gfw_radd", "image_date": date(2026, 11, 1), "confidence": 0.8, "geometry": a},
        {"id": 2, "source": "s2_change", "image_date": date(2026, 11, 5), "confidence": 0.6, "geometry": b},
        {"id": 3, "source": "s2_change", "image_date": date(2026, 11, 5), "confidence": 0.6, "geometry": far},
        {"id": 4, "source": "s1_change", "image_date": date(2026, 9, 1), "confidence": 0.6, "geometry": a},  # too old
    ])
    clusters = sorted(cluster_detections(det), key=lambda c: -len(c.detection_ids))
    assert len(clusters) == 3
    assert sorted(clusters[0].detection_ids) == [1, 2]
    assert clusters[0].sources == {"gfw_radd", "s2_change"} and clusters[0].persistent
    assert clusters[0].area_ha > max(gpd.GeoSeries([a], crs=4326).to_crs(32630).area.iloc[0] / 1e4, 0.1)


def test_new_detection_attaches_to_existing_alert():
    a = box(-0.600, 6.200, -0.595, 6.205)
    det = _det([{"id": 7, "source": "s1_change", "image_date": date(2026, 11, 10), "confidence": 0.6, "geometry": a}])
    existing = gpd.GeoDataFrame({"id": [42], "first_seen": [date(2026, 10, 20)]},
                                geometry=[a.buffer(0.0005)], crs="EPSG:4326")
    [c] = cluster_detections(det, existing)
    assert c.existing_alert_id == 42 and c.persistent and c.first_seen == date(2026, 10, 20)


# --- GFW --------------------------------------------------------------------

def test_gfw_pixels_merge_and_small_clearings_are_dropped():
    ds = next(d for d in DATASETS if d.source == "gfw_glad_s2")
    # 4 × 4 block of 10 m pixels (0.16 ha) + one lone pixel (0.01 ha)
    lat0, lon0 = 6.2, -0.6
    dlat, dlon = 10 / 111_320, 10 / (111_320 * np.cos(np.radians(lat0)))
    rows = [{"latitude": lat0 + i * dlat, "longitude": lon0 + j * dlon, "alert_date": "2026-11-03",
             "confidence": "high" if i == 0 else "nominal"} for i in range(4) for j in range(4)]
    rows.append({"latitude": 6.3, "longitude": -0.7, "alert_date": "2026-11-03", "confidence": "high"})
    out = pixels_to_clearings(pd.DataFrame(rows), ds)
    assert len(out) == 1
    r = out.iloc[0]
    assert r["pixels"] == 16 and r["confidence"] == 0.8 and r["image_date"] == date(2026, 11, 3)
    assert gpd.GeoSeries([r.geometry], crs=4326).to_crs(32630).area.iloc[0] / 1e4 == pytest.approx(0.16, rel=0.1)
    again = pixels_to_clearings(pd.DataFrame(rows), ds)
    assert again.iloc[0]["external_id"] == r["external_id"]         # stable ids → idempotent re-runs


# --- messages ---------------------------------------------------------------

def test_whatsapp_line_matches_spec_format(monkeypatch):
    monkeypatch.setenv("APP_URL", "https://gw.example")
    line = tpl.alert_line({"id": 1234, "level": "red", "area_ha": 2.4, "forest_reserve_name": "Atewa Range FR",
                           "region": "Eastern", "district": "Atiwa West", "river_distance_m": 80,
                           "river_name": "Birim", "score": 86})
    assert line == ("🔴 RED ALERT #1234 | 2.4 ha new clearing | Atewa Range FR, Eastern | "
                    "80 m from Birim River | Score 86 | View: https://gw.example/alerts/1234")


def test_daily_email_escapes_html():
    _, _, html = tpl.daily_email([{"id": 1, "level": "amber", "area_ha": 1, "region": "<b>x</b>", "score": 55}], "1 Nov")
    assert "<b>x</b>" not in html and "&lt;b&gt;" in html


class FakeCursor:
    def __init__(self, db):
        self.db, self._row = db, None
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.db.sql.append(sql)
        if sql.strip().startswith("insert into notifications"):
            key = tuple(params[:3])
            self._row = None if key in self.db.keys else (len(self.db.keys) + 1,)
            self.db.keys.add(key)
        else:
            self._row = None

    def fetchone(self):
        return self._row


class FakeConn:
    def __init__(self):
        self.sql, self.keys = [], set()

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        pass

    def rollback(self):
        pass


def test_notify_never_sends_the_same_alert_twice_and_dry_run_is_default(monkeypatch):
    monkeypatch.delenv("NOTIFY_DRY_RUN", raising=False)
    conn = FakeConn()
    msg = nt.Message("sms", "+233200000000", "test")
    assert nt.dry_run() is True
    assert nt.notify(conn, 5, msg) == "sent"
    assert nt.notify(conn, 5, msg) == "duplicate"


def test_send_retries_then_reports_failure(monkeypatch):
    monkeypatch.setenv("NOTIFY_DRY_RUN", "0")
    calls = []
    monkeypatch.setattr(nt.providers, "sms", lambda m: calls.append(1) or (_ for _ in ()).throw(RuntimeError("down")))
    ok, info = nt.send(nt.Message("sms", "+233", "x"), attempts=3, backoff_s=0)
    assert not ok and len(calls) == 3 and "down" in info


# --- chips ------------------------------------------------------------------

def test_chip_annotation_adds_label_and_scale_bar():
    buf = io.BytesIO()
    Image.new("RGB", (512, 512), (40, 120, 40)).save(buf, "PNG")
    out = Image.open(io.BytesIO(annotate(buf.getvalue(), "Sentinel-2 · after · 1 Nov 2026", 2000)))
    assert out.size == (512, 512)
    assert out.getpixel((15, 500)) != (40, 120, 40)            # scale bar drawn bottom-left


# --- connectors -------------------------------------------------------------

def test_connector_mock_data_normalises(monkeypatch):
    for cls in agencies.ALL:
        monkeypatch.setenv(f"{cls.id.upper()}_MOCK", "1")
        c = cls()
        recs = c.normalise(c.fetch())
        assert isinstance(recs, list)
    cad = agencies.Cadastre()
    statuses = [r["status"] for r in cad.normalise(cad.fetch())]
    assert statuses == ["active", "expired"]


def test_unconfigured_connector_is_skipped_not_fatal(monkeypatch):
    monkeypatch.delenv("CERSGIS_NRT_MOCK", raising=False)
    monkeypatch.delenv("CERSGIS_NRT_URL", raising=False)
    result = agencies.CersgisNRT().run(FakeConn())
    assert "skipped" in result


# --- retraining helpers -----------------------------------------------------

def test_f05_and_geographic_holdout():
    y = np.array([1, 1, 0, 0])
    assert f05(y, np.array([0.9, 0.8, 0.1, 0.2]), 0.43) == pytest.approx(1.0)
    assert f05(y, np.array([0.1, 0.1, 0.1, 0.1]), 0.43) == 0.0
    train, test = split_by_region([{"region": "Central"}, {"region": "Eastern"}], "Central")
    assert len(train) == 1 and test[0]["region"] == "Central"


# --- spatial context ----------------------------------------------------------

def test_context_finds_reserve_river_distance_and_concession():
    from shapely.geometry import LineString
    from galamsey.context import ReferenceIndex

    reserve = gpd.GeoDataFrame({"name": ["Atewa Range FR"]}, geometry=[box(-0.62, 6.18, -0.55, 6.25)], crs=4326)
    river = gpd.GeoDataFrame({"name": ["Birim"]}, geometry=[LineString([(-0.6, 6.10), (-0.6, 6.30)])], crs=4326)
    conc = gpd.GeoDataFrame({"name": ["L1"]}, geometry=[box(-0.80, 6.0, -0.70, 6.1)], crs=4326)
    idx = ReferenceIndex({"forest_reserve": reserve, "river": river, "concession": conc})
    site = gpd.GeoSeries([box(-0.5985, 6.20, -0.5975, 6.201)], crs=4326).to_crs(32630).iloc[0]
    ctx = idx.context_for(site)
    assert ctx.in_forest_reserve and ctx.forest_reserve_name == "Atewa Range FR"
    assert ctx.river_name == "Birim" and 140 < ctx.river_distance_m < 190
    assert ctx.in_concession is False
    assert ReferenceIndex({}).context_for(site).in_concession is None     # unknown, not "outside"
