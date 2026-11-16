"""Daily operations.

  python -m galamsey.daily detect      # 05:00 GMT — connectors, GFW, Sentinel-2, Sentinel-1, fusion, scoring, chips
  python -m galamsey.daily notify      # 07:00 GMT — new Red/Amber alerts by WhatsApp/SMS/email, daily summary email
  python -m galamsey.daily weekly      # Mondays    — Watch-level digest + trend stats
  python -m galamsey.daily connectors  # agency feeds only

Reliability: every run is a row in pipeline_runs. Tiles are marked processed one by
one, so a failed run resumes where it stopped. Inserting a detection twice is a no-op
(unique source + external_id), so re-running a day is safe.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import logging
import os
from datetime import date, datetime, timedelta, timezone

import geopandas as gpd
import pandas as pd
from shapely import wkb

from . import db
from .context import ReferenceIndex
from .fusion import Cluster, cluster_detections, cluster_geometry_wgs84
from .geo import UTM30N, WGS84
from .notify import Message, notify
from .notify import templates as tpl
from .scoring import AlertFacts, apply_daily_cap, score_alert

log = logging.getLogger("daily")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def settings(conn) -> dict:
    with conn.cursor() as cur:
        cur.execute("select key, value from settings")
        return {k: v for k, v in cur.fetchall()}


def reference_layers(conn) -> dict[str, gpd.GeoDataFrame]:
    out = {}
    for layer in ("admin0", "admin1", "admin2", "forest_reserve", "river", "concession", "priority_zone"):
        gdf = db.read_layer(conn, layer)
        out[layer] = gdf.rename_geometry("geometry") if not gdf.empty else None
    return out


def ee_available() -> bool:
    return bool(os.environ.get("GEE_SERVICE_ACCOUNT_KEY") or os.environ.get("GEE_LOCAL_AUTH") == "1")


def insert_detections(conn, records: list[dict]) -> int:
    n = 0
    with conn.cursor() as cur:
        for r in records:
            geom = r["geometry"]
            if geom.geom_type in ("LineString", "MultiLineString"):
                continue
            cur.execute("""insert into detections (source, geom, image_date, confidence, metrics, tile_id, external_id)
                           values (%s, st_multi(st_collectionextract(st_makevalid(st_setsrid(st_geomfromwkb(%s),4326)),3)),
                                   %s, %s, %s::jsonb, %s, %s)
                           on conflict (source, external_id) do nothing""",
                        (r["source"], geom.wkb, r["image_date"], r.get("confidence"),
                         json.dumps({**r.get("metrics", {}), "cloud_affected": bool(r.get("cloud_affected"))}),
                         r.get("tile_id"), r["external_id"]))
            n += cur.rowcount
    conn.commit()
    return n


# ---------------------------------------------------------------------------
# 05:00 — detection
# ---------------------------------------------------------------------------

def step_gfw(conn, run: db.RunLog, refs) -> None:
    from .detect.gfw import fetch_gfw

    with conn.cursor() as cur:
        cur.execute("select max(image_date) from detections where source like 'gfw_%'")
        last = cur.fetchone()[0]
    since = (last or date.today() - timedelta(days=30)) - timedelta(days=3)   # GFW back-fills a few days
    area = refs["admin1"] if refs["admin1"] is not None else refs["admin0"]
    clearings, errors = fetch_gfw(area, since)
    for e in errors:
        run.error(f"gfw: {e}")
    recs = [{**r, "metrics": {"pixels": int(r["pixels"])}} for r in clearings.to_dict("records")] if not clearings.empty else []
    run.details["gfw_new_detections"] = insert_detections(conn, recs)


def tiles_to_process(conn, resume_from: str | None) -> list[tuple[str, object, str]]:
    with conn.cursor() as cur:
        # All tiles not yet done today, mining-belt tiles first. Tiles with no new imagery
        # finish almost instantly (the scene lookup returns nothing).
        cur.execute("""select id, st_asbinary(geom), priority from tiles
                       where last_processed_at is null or last_processed_at < date_trunc('day', now())
                       order by priority = 'high' desc, id""")
        rows = [(i, wkb.loads(bytes(g)), p) for i, g, p in cur.fetchall()]
    if resume_from:
        rows = [r for r in rows if r[0] >= resume_from] + [r for r in rows if r[0] < resume_from]
    return rows


def river_segments_for(refs, tile_geom) -> list[dict]:
    rivers = refs.get("river")
    if rivers is None:
        return []
    hits = rivers[rivers.intersects(tile_geom)]
    if hits.empty:
        return []
    segs = gpd.GeoDataFrame(hits[["name"]], geometry=hits.geometry.intersection(tile_geom), crs=WGS84)
    segs = segs.to_crs(UTM30N).dissolve(by="name", as_index=False)
    segs["geometry"] = segs.geometry.buffer(30)          # water surface, not the bank
    return [{"name": n, "geometry": g} for n, g in zip(segs["name"], segs.to_crs(WGS84).geometry)]


def step_satellite(conn, run: db.RunLog, refs, cfg) -> None:
    if not ee_available() and not os.environ.get("CDSE_CLIENT_ID"):
        run.error("satellite: neither Earth Engine nor Copernicus credentials configured; Sources B and C skipped")
        return
    with conn.cursor() as cur:
        cur.execute("select last_tile_id from pipeline_runs where job='daily_detection' and status='failed' "
                    "and started_at > now() - interval '1 day' order by id desc limit 1")
        row = cur.fetchone()
    tiles = tiles_to_process(conn, row[0] if row else None)
    max_cloud = float(cfg.get("s2_max_cloud_pct", 60))
    since = date.today() - timedelta(days=3)

    for tile_id, geom, _prio in tiles:
        found = []
        try:
            if ee_available():
                from .detect import sentinel1, sentinel2
                for day in sentinel2.new_scene_dates(geom, since, date.today()):
                    recs, stats = sentinel2.detect_tile(geom, day, tile_id, max_cloud, river_segments_for(refs, geom))
                    found += recs
                    run.details.setdefault("scenes", []).append(stats)
                for scene in sentinel1.new_scenes(geom, since, date.today()):
                    recs, stats = sentinel1.detect_tile(geom, scene, tile_id)
                    found += recs
                    run.details.setdefault("scenes", []).append(stats)
            else:
                from .detect import copernicus
                found += copernicus.detect_tile(geom, date.today() - timedelta(days=1), tile_id)
            turbidity = [r for r in found if r["source"] == "s2_turbidity"]
            insert_detections(conn, [r for r in found if r["source"] != "s2_turbidity"])
            for t in turbidity:
                upsert_turbidity_alert(conn, t, refs)
            with conn.cursor() as cur:
                cur.execute("update tiles set last_processed_at = now() where id = %s", (tile_id,))
                cur.execute("update pipeline_runs set tiles_processed = tiles_processed + 1, last_tile_id = %s, "
                            "scenes_used = %s where id = %s", (tile_id, len(run.details.get("scenes", [])), run.id))
            conn.commit()
        except Exception as exc:
            conn.rollback()
            run.error(f"tile {tile_id}: {type(exc).__name__}: {exc}")
            with conn.cursor() as cur:
                cur.execute("update pipeline_runs set last_tile_id = %s where id = %s", (tile_id, run.id))
            conn.commit()
            if len(run.errors) > 50:
                raise RuntimeError("Too many tile failures; stopping so the next run can resume") from exc


def upsert_turbidity_alert(conn, t: dict, refs) -> None:
    """River turbidity alerts are per river segment and per day (spec 5.3)."""
    g = gpd.GeoSeries([t["geometry"]], crs=WGS84)
    area = float(g.to_crs(UTM30N).area.iloc[0] / 10_000)
    ctx = ReferenceIndex({k: refs[k] for k in ("admin1", "admin2") if refs.get(k) is not None}).context_for(
        g.to_crs(UTM30N).iloc[0])
    with conn.cursor() as cur:
        cur.execute("""insert into alerts (geom, area_ha, score, score_breakdown, level, region, district,
                         river_distance_m, river_name, first_seen, last_seen, sources)
                       select st_multi(st_setsrid(st_geomfromwkb(%s),4326)), %s, 60, %s::jsonb, 'amber', %s, %s, 0, %s, %s, %s, '{s2_turbidity}'
                       where not exists (select 1 from alerts where 's2_turbidity' = any(sources) and river_name = %s
                                         and last_seen >= %s::date - 7 and st_intersects(geom, st_setsrid(st_geomfromwkb(%s),4326)))""",
                    (t["geometry"].wkb, round(area, 2), json.dumps({"river_turbidity": 60, **t["metrics"]}),
                     ctx.region, ctx.district, t["metrics"].get("river"), t["image_date"], t["image_date"],
                     t["metrics"].get("river"), t["image_date"], t["geometry"].wkb))


def unattached_detections(conn, days: int = 30) -> gpd.GeoDataFrame:
    return gpd.read_postgis(
        """select d.id, d.source, d.image_date, d.confidence, coalesce((d.metrics->>'cloud_affected')::boolean, false) as cloud_affected, d.geom
           from detections d where d.image_date >= current_date - %(days)s
             and not exists (select 1 from alert_sources s where s.detection_id = d.id)""",
        conn, geom_col="geom", params={"days": days}).rename_geometry("geometry")


def open_alerts(conn) -> gpd.GeoDataFrame:
    return gpd.read_postgis(
        """select id, first_seen, last_seen, sources, ai_probability, geom from alerts
           where status not in ('rejected', 'closed') and last_seen >= current_date - 60
             and not ('s2_turbidity' = any(sources))""",
        conn, geom_col="geom").rename_geometry("geometry")


def step_fuse(conn, run: db.RunLog, refs, cfg) -> list[int]:
    """Merge unattached detections into alerts, score them, return ids of new alerts."""
    det = unattached_detections(conn)
    if det.empty:
        return []
    existing = open_alerts(conn)
    clusters = cluster_detections(det, existing, window_days=int(cfg.get("fusion_window_days", 7)))
    index = ReferenceIndex(refs)
    weights = cfg.get("score_weights") or {}
    thresholds = {"red": cfg.get("red_min_score", 75), "amber": cfg.get("amber_min_score", 50),
                  "watch": cfg.get("watch_min_score", 30)}
    min_ha = float(cfg.get("min_alert_area_ha", 0.1))
    created: list[int] = []
    use_ai = os.environ.get("AI_CLASSIFIER", "1") == "1" and ee_available()

    for c in clusters:
        if c.area_ha < min_ha:
            continue
        ctx = index.context_for(c.geom_m)
        geom = cluster_geometry_wgs84(c)
        prev_sources, prev_ai = set(), None
        if c.existing_alert_id is not None:
            row = existing[existing["id"] == c.existing_alert_id].iloc[0]
            prev_sources, prev_ai = set(row["sources"] or []), row["ai_probability"]
        sources = sorted(c.sources | prev_sources)
        ai = prev_ai if prev_ai is not None and not pd.isna(prev_ai) else None
        if ai is None and use_ai and c.area_ha >= 0.3:
            from .ai.classifier import score_alert_point
            pt = geom.representative_point()
            ai = score_alert_point(pt.x, pt.y, c.last_seen)
        s = score_alert(AlertFacts(sources=sources, area_ha=c.area_ha, max_confidence=c.max_confidence,
                                   ai_probability=ai, persistent=c.persistent,
                                   in_forest_reserve=ctx.in_forest_reserve, river_distance_m=ctx.river_distance_m,
                                   in_concession=ctx.in_concession, cloud_affected=c.cloud_affected),
                        weights, thresholds)
        if s.level is None and c.existing_alert_id is None:
            continue           # below Watch: keep the detections, no alert yet
        level = s.level or "watch"
        params = dict(geom=geom.wkb, area=round(c.area_ha, 2), score=s.score, br=json.dumps(s.breakdown), level=level,
                      region=ctx.region, district=ctx.district, res=ctx.in_forest_reserve, resn=ctx.forest_reserve_name,
                      rd=ctx.river_distance_m, rn=ctx.river_name, conc=bool(ctx.in_concession),
                      first=c.first_seen, last=c.last_seen, sources=sources, ai=ai)
        with conn.cursor() as cur:
            if c.existing_alert_id is None:
                cur.execute("""insert into alerts (geom, area_ha, score, score_breakdown, level, region, district,
                                 in_forest_reserve, forest_reserve_name, river_distance_m, river_name, in_concession,
                                 first_seen, last_seen, sources, ai_probability)
                               values (st_multi(st_setsrid(st_geomfromwkb(%(geom)s),4326)), %(area)s, %(score)s, %(br)s::jsonb,
                                 %(level)s, %(region)s, %(district)s, %(res)s, %(resn)s, %(rd)s, %(rn)s, %(conc)s,
                                 %(first)s, %(last)s, %(sources)s, %(ai)s) returning id""", params)
                alert_id = cur.fetchone()[0]
                created.append(alert_id)
            else:
                alert_id = c.existing_alert_id
                cur.execute("""update alerts set geom = st_multi(st_setsrid(st_geomfromwkb(%(geom)s),4326)), area_ha=%(area)s,
                                 score=%(score)s, score_breakdown=%(br)s::jsonb, level=%(level)s, sources=%(sources)s,
                                 ai_probability=%(ai)s, last_seen=greatest(last_seen, %(last)s),
                                 in_forest_reserve=%(res)s, forest_reserve_name=%(resn)s, river_distance_m=%(rd)s,
                                 river_name=%(rn)s, in_concession=%(conc)s
                               where id=%(id)s""", {**params, "id": alert_id})
            cur.executemany("insert into alert_sources (alert_id, detection_id) values (%s, %s) on conflict do nothing",
                            [(alert_id, int(d)) for d in c.detection_ids])
        conn.commit()
    with conn.cursor() as cur:
        cur.execute("update pipeline_runs set alerts_created = %s where id = %s", (len(created), run.id))
    conn.commit()
    return created


def step_chips(conn, run: db.RunLog, alert_ids: list[int]) -> None:
    if not alert_ids or not ee_available() or not os.environ.get("SUPABASE_SERVICE_ROLE_KEY"):
        return
    from .chips import render_chips, upload
    with conn.cursor() as cur:
        cur.execute("select id, st_x(centroid), st_y(centroid), first_seen, last_seen from alerts where id = any(%s)",
                    (alert_ids,))
        rows = cur.fetchall()
    for aid, lon, lat, first, last in rows:
        try:
            paths = {name: upload("chips", f"{aid}/{name}.png", png)
                     for name, png in render_chips(lon, lat, first, last).items()}
            with conn.cursor() as cur:
                cur.execute("update alerts set chips = %s::jsonb where id = %s", (json.dumps(paths), aid))
            conn.commit()
        except Exception as exc:
            conn.rollback()
            run.error(f"chips {aid}: {exc}")


def detect(conn) -> str:
    run = db.RunLog(conn, "daily_detection")
    cfg = settings(conn)
    try:
        refs = reference_layers(conn)
        if refs["admin0"] is None:
            raise RuntimeError("Reference data not loaded. Run: python -m galamsey.ingest_reference all")
        from .connectors.agencies import run_all
        run.details["connectors"] = run_all(conn)
        step_gfw(conn, run, refs)
        step_satellite(conn, run, refs, cfg)
        created = step_fuse(conn, run, refs, cfg)
        run.details["new_alerts"] = len(created)
        step_chips(conn, run, created)
        status = "partial" if run.errors else "success"
    except Exception as exc:
        conn.rollback()
        run.error(f"fatal: {type(exc).__name__}: {exc}")
        status = "failed"
    run.finish(status)
    if status == "failed":
        alert_admins(conn, f"Daily detection run {run.id} failed: {run.errors[-1]['message']}")
    return status


# ---------------------------------------------------------------------------
# 07:00 — notifications
# ---------------------------------------------------------------------------

ALERT_COLS = ("id", "level", "score", "area_ha", "region", "district", "forest_reserve_name",
              "river_distance_m", "river_name", "lon", "lat")


def recipients_for(conn, region: str | None, level: str) -> list[tuple[str, str]]:
    with conn.cursor() as cur:
        cur.execute("""select channel, address from recipients
                       where active and (region is null or region = %s) and %s::alert_level = any(levels)""",
                    (region, level))
        return cur.fetchall()


def map_thumbnail(conn, alerts: list[dict]) -> bytes:
    """Small PNG of Ghana with today's alerts as dots, for the email."""
    from PIL import Image, ImageDraw

    gh = db.read_layer(conn, "admin0")
    W, H, (minx, miny, maxx, maxy) = 560, 760, (-3.35, 4.6, 1.3, 11.25)
    sx = lambda x: (x - minx) / (maxx - minx) * W
    sy = lambda y: H - (y - miny) / (maxy - miny) * H
    im = Image.new("RGB", (W, H), (17, 24, 22))
    d = ImageDraw.Draw(im)
    for g in gh.geometry:
        for poly in getattr(g, "geoms", [g]):
            d.polygon([(sx(x), sy(y)) for x, y in poly.exterior.coords], outline=(163, 177, 172), fill=(24, 33, 30))
    colours = {"red": (239, 68, 68), "amber": (245, 158, 11)}
    for a in sorted(alerts, key=lambda a: a["level"] == "red"):
        x, y = sx(a["lon"]), sy(a["lat"])
        d.ellipse([x - 6, y - 6, x + 6, y + 6], fill=colours.get(a["level"], (156, 163, 175)), outline=(255, 255, 255))
    buf = io.BytesIO()
    im.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def send_daily(conn) -> dict:
    cfg = settings(conn)
    with conn.cursor() as cur:
        cur.execute("""select id, level, score, area_ha, region, district, forest_reserve_name, river_distance_m, river_name,
                               st_x(centroid), st_y(centroid)
                        from alerts where level in ('red','amber') and notified_at is null and not digest_only
                          and status in ('new','reviewing','confirmed') and created_at > now() - interval '3 days'""")
        alerts = [dict(zip(ALERT_COLS, r)) for r in cur.fetchall()]
    alerts = apply_daily_cap(alerts, int(cfg.get("max_alerts_per_region_day", 15)))
    held = [a["id"] for a in alerts if a["digest_only"]]
    send = [a for a in alerts if not a["digest_only"]]
    counts = {"sent": 0, "failed": 0, "duplicate": 0, "held_by_cap": len(held)}

    for a in send:
        line = tpl.alert_line(a)
        for channel, address in recipients_for(conn, a["region"], a["level"]):
            if channel in ("whatsapp", "sms"):
                counts[notify(conn, a["id"], Message(channel, address, line, template_params=[line]))] += 1

    # One summary email per email recipient, covering the alerts in their scope.
    with conn.cursor() as cur:
        cur.execute("select address, region, levels from recipients where active and channel = 'email'")
        emails = cur.fetchall()
    day = date.today().strftime("%d %b %Y")
    for address, region, levels in emails:
        mine = [a for a in send if (region is None or a["region"] == region) and a["level"] in levels]
        if not mine:
            continue
        subject, text, html = tpl.daily_email(mine, day, map_cid="map")
        png = base64.b64encode(map_thumbnail(conn, mine)).decode()
        msg = Message("email", address, text, subject=subject, html=html,
                      attachments=[{"filename": "map.png", "content": png, "content_id": "map"}])
        counts[notify(conn, None, msg)] += 1

    with conn.cursor() as cur:
        if send:
            cur.execute("update alerts set notified_at = now() where id = any(%s)", ([a["id"] for a in send],))
        if held:
            cur.execute("update alerts set digest_only = true where id = any(%s)", (held,))
    conn.commit()
    return counts


def send_weekly(conn) -> dict:
    with conn.cursor() as cur:
        cur.execute("""select id, level, score, area_ha, region, district, forest_reserve_name, river_distance_m, river_name,
                               st_x(centroid), st_y(centroid)
                        from alerts where created_at > now() - interval '7 days'
                          and (level = 'watch' or digest_only) order by score desc""")
        watch = [dict(zip(ALERT_COLS, r)) for r in cur.fetchall()]
        cur.execute("""select count(*), coalesce(sum(area_ha),0),
                              count(*) filter (where status in ('confirmed','field_verified','closed')),
                              count(*) filter (where status = 'rejected')
                       from alerts where created_at > now() - interval '7 days'""")
        n, ha, conf, rej = cur.fetchone()
        cur.execute("select address, region from recipients where active and weekly_digest and channel = 'email'")
        emails = cur.fetchall()
    stats = {"alerts": n, "hectares": float(ha), "confirmed": conf, "rejected": rej}
    week = (date.today() - timedelta(days=7)).strftime("%d %b %Y")
    counts = {"sent": 0, "failed": 0, "duplicate": 0}
    for address, region in emails:
        mine = [a for a in watch if region is None or a["region"] == region]
        subject, text, html = tpl.weekly_digest(mine, stats, week)
        counts[notify(conn, None, Message("email", address, text, subject=subject, html=html))] += 1
    return counts


def alert_admins(conn, text: str) -> None:
    """Pipeline failure → email every admin (best effort)."""
    try:
        with conn.cursor() as cur:
            cur.execute("select u.email from profiles p join auth.users u on u.id = p.user_id where p.role = 'admin'")
            for (email,) in cur.fetchall():
                notify(conn, None, Message("email", email, text, subject="Galamsey Watch: pipeline problem"))
    except Exception as exc:
        log.error("Could not alert admins: %s", exc)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m galamsey.daily")
    ap.add_argument("job", choices=["detect", "notify", "weekly", "connectors"])
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    with db.connect() as conn:
        if args.job == "detect":
            status = detect(conn)
            log.info("detect: %s", status)
            return 0 if status != "failed" else 1
        if args.job == "notify":
            log.info("notify: %s", send_daily(conn))
        elif args.job == "weekly":
            log.info("weekly: %s", send_weekly(conn))
        else:
            from .connectors.agencies import run_all
            log.info("connectors: %s", run_all(conn))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
