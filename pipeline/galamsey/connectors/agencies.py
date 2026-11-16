"""One adapter per agency. Field names in normalise() match the mock files and are the
format we will ASK each agency for (docs/agency-data-requests.md). If an agency's
real format differs, only its normalise() changes."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import requests
from shapely.geometry import Point, shape

from .base import Connector, parse_time


class EquipmentGPS(Connector):
    """Minerals Commission excavator / equipment tracking.
    A machine sitting outside a licensed concession, in a forest reserve or within
    500 m of a river becomes a 'gov_equipment' detection, which fusion merges with
    any satellite detection there (boosting its score) or turns into a new alert."""
    id = "minerals_commission_equipment"
    agency = "Minerals Commission"

    def normalise(self, raw):
        return [{"device_id": str(r["device_id"]), "lon": float(r["lon"]), "lat": float(r["lat"]),
                 "recorded_at": parse_time(r["recorded_at"])} for r in raw]

    def load(self, conn, records):
        n = 0
        with conn.cursor() as cur:
            for r in records:
                cur.execute("""
                    with p as (select st_setsrid(st_makepoint(%(lon)s, %(lat)s), 4326) g)
                    insert into equipment_positions (connector_id, device_id, geom, recorded_at,
                                                     in_concession, in_reserve, river_distance_m)
                    select %(cid)s, %(device_id)s, p.g, %(recorded_at)s,
                      exists (select 1 from reference_layers where type='concession' and st_intersects(geom, p.g)),
                      exists (select 1 from reference_layers where type='forest_reserve' and st_intersects(geom, p.g)),
                      (select round(min(st_distance(geom::geography, p.g::geography))) from reference_layers
                        where type='river' and st_dwithin(geom::geography, p.g::geography, 5000))
                    from p
                    on conflict (connector_id, device_id, recorded_at) do nothing
                    returning id, in_concession, in_reserve, river_distance_m""", {**r, "cid": self.id})
                row = cur.fetchone()
                if not row:
                    continue
                n += 1
                _, in_conc, in_res, river_m = row
                if not in_conc and (in_res or (river_m is not None and river_m <= 500)):
                    # 50 m radius footprint around the machine as a detection.
                    cur.execute("""
                        insert into detections (source, geom, image_date, confidence, metrics, external_id)
                        values ('gov_equipment',
                                st_multi(st_buffer(st_setsrid(st_makepoint(%s,%s),4326)::geography, 50)::geometry),
                                %s::date, 0.7, %s::jsonb, %s)
                        on conflict (source, external_id) do nothing""",
                        (r["lon"], r["lat"], r["recorded_at"], json.dumps({"device_id": r["device_id"]}),
                         f"equip|{r['device_id']}|{r['recorded_at']:%Y-%m-%d}"))
        return n


class Cadastre(Connector):
    """Minerals Commission cadastre: refreshes concession boundaries and licence status.
    Only 'active' licences count as licensed when scoring."""
    id = "minerals_commission_cadastre"
    agency = "Minerals Commission"

    def normalise(self, raw):
        out = []
        for f in raw:
            props = f.get("properties", f)
            out.append({"name": props.get("licence_no") or props.get("name"),
                        "holder": props.get("holder"), "status": (props.get("status") or "active").lower(),
                        "licence_type": props.get("licence_type"), "expires": props.get("expires"),
                        "geometry": shape(f["geometry"])})
        return out

    def load(self, conn, records):
        batch = uuid.uuid4()
        now = datetime.now(timezone.utc)
        with conn.cursor() as cur:
            for r in records:
                if r["status"] != "active":
                    continue
                attrs = {k: r[k] for k in ("holder", "status", "licence_type", "expires")}
                cur.execute("""insert into reference_layers (type, name, geom, attributes, source, retrieved_at, licence, verified, batch_id)
                               values ('concession', %s, st_setsrid(st_geomfromwkb(%s), 4326), %s::jsonb,
                                       'minerals_commission:cadastre', %s, 'Per Minerals Commission data-sharing terms', true, %s)""",
                            (r["name"], r["geometry"].wkb, json.dumps(attrs), now, batch))
            cur.execute("select swap_reference_batch('concession', %s)", (batch,))
        return sum(r["status"] == "active" for r in records)


class _DetectionFeed(Connector):
    source = ""

    def normalise(self, raw):
        out = []
        for f in raw:
            props = f.get("properties", f)
            out.append({"external_id": str(props.get("id")), "date": str(props.get("date"))[:10],
                        "confidence": float(props.get("confidence", 0.7)), "geometry": shape(f["geometry"])})
        return out

    def load(self, conn, records):
        n = 0
        with conn.cursor() as cur:
            for r in records:
                cur.execute("""insert into detections (source, geom, image_date, confidence, metrics, external_id)
                               values (%s, st_multi(st_setsrid(st_geomfromwkb(%s), 4326)), %s, %s, %s::jsonb, %s)
                               on conflict (source, external_id) do nothing""",
                            (self.source, r["geometry"].wkb, r["date"], r["confidence"],
                             json.dumps({"agency": self.agency}), r["external_id"]))
                n += cur.rowcount
        return n


class ForestryCommissionMining(_DetectionFeed):
    """Forestry Commission mapped mining areas → extra detection source (and, once
    reviewed, training labels via the normal review flow)."""
    id = "forestry_commission_mining"
    agency = "Forestry Commission"
    source = "gov_forestry"


class CersgisNRT(_DetectionFeed):
    id = "cersgis_nrt"
    agency = "CERSGIS"
    source = "gov_cersgis"


class WaterQuality(Connector):
    """WRC station readings, shown next to turbidity alerts on nearby rivers."""
    id = "wrc_water_quality"
    agency = "Water Resources Commission"

    def normalise(self, raw):
        return [{"station_id": str(r["station_id"]), "station_name": r.get("station_name"),
                 "lon": float(r["lon"]), "lat": float(r["lat"]), "measured_at": parse_time(r["measured_at"]),
                 "turbidity_ntu": r.get("turbidity_ntu")} for r in raw]

    def load(self, conn, records):
        n = 0
        with conn.cursor() as cur:
            for r in records:
                cur.execute("""insert into water_quality_readings (connector_id, station_id, station_name, geom, measured_at, turbidity_ntu)
                               values (%s, %s, %s, st_setsrid(st_makepoint(%s,%s),4326), %s, %s)
                               on conflict do nothing""",
                            (self.id, r["station_id"], r["station_name"], r["lon"], r["lat"], r["measured_at"], r["turbidity_ntu"]))
                n += cur.rowcount
        return n


class Naimos(Connector):
    """NAIMOS (enforcement) two-way link: push newly confirmed alerts, pull operation
    outcomes (raided / closed / reclaimed) into the alert timeline."""
    id = "naimos"
    agency = "NAIMOS"

    def normalise(self, raw):
        return [{"alert_id": int(r["alert_id"]), "action": r["action"], "action_date": str(r["action_date"])[:10],
                 "notes": r.get("notes")} for r in raw]

    def push_confirmed(self, conn) -> int:
        """Send confirmed alerts the link has not sent yet. Mock mode only logs."""
        with conn.cursor() as cur:
            cur.execute("""select a.id, st_y(a.centroid), st_x(a.centroid), a.area_ha, a.region, a.district
                           from alerts a where a.status in ('confirmed','field_verified')
                             and not exists (select 1 from enforcement_actions e where e.alert_id=a.id and e.action='shared')""")
            rows = cur.fetchall()
            if not rows:
                return 0
            if not self.mock:
                if not self.url:
                    return 0
                payload = [dict(zip(("alert_id", "lat", "lon", "area_ha", "region", "district"), map(_plain, r))) for r in rows]
                requests.post(self.url.rstrip("/") + "/alerts", json=payload, timeout=60,
                              headers={"Authorization": f"Bearer {self.token}"}).raise_for_status()
            for r in rows:
                cur.execute("""insert into enforcement_actions (alert_id, agency, action, action_date, notes)
                               values (%s, 'NAIMOS', 'shared', current_date, %s)""",
                            (r[0], "mock: not actually sent" if self.mock else None))
        return len(rows)

    def load(self, conn, records):
        n = 0
        with conn.cursor() as cur:
            for r in records:
                cur.execute("""insert into enforcement_actions (alert_id, agency, action, action_date, notes)
                               select %s, 'NAIMOS', %s, %s, %s where exists (select 1 from alerts where id=%s)
                                 and not exists (select 1 from enforcement_actions where alert_id=%s and action=%s and action_date=%s)""",
                            (r["alert_id"], r["action"], r["action_date"], r["notes"], r["alert_id"],
                             r["alert_id"], r["action"], r["action_date"]))
                n += cur.rowcount
        n += self.push_confirmed(conn)
        return n


def _plain(v):
    return float(v) if hasattr(v, "as_integer_ratio") and not isinstance(v, int) else v


ALL = [Cadastre, EquipmentGPS, ForestryCommissionMining, CersgisNRT, WaterQuality, Naimos]


def run_all(conn) -> list[dict]:
    return [cls().run(conn) for cls in ALL]
