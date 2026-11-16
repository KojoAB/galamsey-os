"""Validation (spec 12): before trusting the pipeline, check it against known,
publicly reported galamsey areas and measure precision on a hand-checked sample.

  python -m galamsey.validate sample --n 50 --out sample.csv   # random alerts, stratified by level, for hand-checking
  python -m galamsey.validate report --ids sample.csv          # precision of that sample from analyst reviews
  python -m galamsey.validate known-sites                      # do we detect change at the reference sites?

Reference sites below are areas widely reported in Ghanaian and international media
as affected by galamsey. Use dates on both sides of a reported incident.
"""

from __future__ import annotations

import argparse
import csv
from datetime import date

from shapely.geometry import box

from . import db

KNOWN_SITES = {
    "Atewa Range FR (Eastern)": box(-0.65, 6.10, -0.50, 6.30),
    "Birim corridor near Kibi (Eastern)": box(-0.60, 6.05, -0.45, 6.20),
    "Offin river, Dunkwa (Central)": box(-1.85, 5.90, -1.70, 6.05),
    "Pra river, Twifo Praso (Central)": box(-1.60, 5.55, -1.45, 5.70),
    "Ankobra, Prestea (Western)": box(-2.20, 5.35, -2.05, 5.50),
}


def precision(rows: list[tuple[str, str]]) -> dict:
    """rows: (alert_id, decision) with decision confirm/reject. Unreviewed rows are ignored."""
    conf = sum(1 for _, d in rows if d == "confirm")
    rej = sum(1 for _, d in rows if d == "reject")
    n = conf + rej
    return {"reviewed": n, "confirmed": conf, "rejected": rej, "precision": round(conf / n, 3) if n else None}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m galamsey.validate")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sample"); s.add_argument("--n", type=int, default=50); s.add_argument("--out", default="sample.csv")
    r = sub.add_parser("report"); r.add_argument("--ids", required=True)
    k = sub.add_parser("known-sites"); k.add_argument("--start", default="2025-11-01"); k.add_argument("--end", default=str(date.today()))
    args = ap.parse_args(argv)

    if args.cmd == "known-sites":
        from .detect import sentinel2
        for name, geom in KNOWN_SITES.items():
            days = sentinel2.new_scene_dates(geom, date.fromisoformat(args.start), date.fromisoformat(args.end))
            found = 0
            for d in days[-6:]:
                recs, _ = sentinel2.detect_tile(geom, d, f"validation:{name}")
                found += len(recs)
            print(f"{name}: {len(days)} scenes, {found} detections in the last {min(6, len(days))} scenes")
        return 0

    with db.connect() as conn, conn.cursor() as cur:
        if args.cmd == "sample":
            per = max(1, args.n // 3)
            rows = []
            for level in ("red", "amber", "watch"):
                cur.execute("select id, level, score, region, district, st_y(centroid), st_x(centroid) from alerts "
                            "where level = %s order by random() limit %s", (level, per))
                rows += cur.fetchall()
            with open(args.out, "w", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(["alert_id", "level", "score", "region", "district", "lat", "lon"])
                w.writerows(rows)
            print(f"Wrote {len(rows)} alerts to {args.out}. Review each in the dashboard, then run: report --ids {args.out}")
        else:
            ids = [int(r["alert_id"]) for r in csv.DictReader(open(args.ids))]
            cur.execute("""select distinct on (alert_id) alert_id::text, decision::text from reviews
                           where alert_id = any(%s) and decision in ('confirm','reject') order by alert_id, created_at desc""", (ids,))
            print(precision(cur.fetchall()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
