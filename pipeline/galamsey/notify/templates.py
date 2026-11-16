"""Message wording (spec 7). Kept short for WhatsApp and SMS."""

from __future__ import annotations

import html
import os

ICON = {"red": "🔴", "amber": "🟠", "watch": "⚪"}


def app_url(path: str = "") -> str:
    return os.environ.get("APP_URL", "https://galamsey-watch.vercel.app").rstrip("/") + path


def alert_line(a: dict) -> str:
    """🔴 RED ALERT #1234 | 2.4 ha new clearing | Atewa Range FR, Eastern | 80 m from Birim River | Score 86 | View: <link>"""
    parts = [f"{ICON.get(a['level'], '')} {a['level'].upper()} ALERT #{a['id']}".strip(),
             f"{float(a['area_ha']):.1f} ha new clearing"]
    place = ", ".join(p for p in (a.get("forest_reserve_name"), a.get("district") if not a.get("forest_reserve_name") else None,
                                  a.get("region")) if p)
    if place:
        parts.append(place)
    if a.get("river_distance_m") is not None and a["river_distance_m"] <= 500 and a.get("river_name"):
        river = a["river_name"] if a["river_name"].lower().endswith("river") else f"{a['river_name']} River"
        parts.append(f"{int(a['river_distance_m'])} m from {river}")
    parts.append(f"Score {a['score']}")
    parts.append(f"View: {app_url(f'/alerts/{a['id']}')}")
    return " | ".join(parts)


def daily_email(alerts: list[dict], day: str, map_cid: str | None = None) -> tuple[str, str, str]:
    """(subject, text, html) for the 07:00 summary."""
    reds = sum(a["level"] == "red" for a in alerts)
    ambers = sum(a["level"] == "amber" for a in alerts)
    subject = f"Galamsey Watch {day}: {reds} red, {ambers} amber"
    text = "\n".join([subject, ""] + [alert_line(a) for a in alerts] +
                     ["", "Every alert must be reviewed before it is treated as fact."])
    rows = "".join(
        f"<tr><td>{ICON.get(a['level'], '')} {a['level'].title()}</td><td><a href=\"{app_url(f'/alerts/{a['id']}')}\">#{a['id']}</a></td>"
        f"<td>{float(a['area_ha']):.1f}</td><td>{html.escape(a.get('district') or '')}, {html.escape(a.get('region') or '')}</td>"
        f"<td>{html.escape(a.get('forest_reserve_name') or '—')}</td>"
        f"<td>{'' if a.get('river_distance_m') is None else str(a['river_distance_m']) + ' m'}</td><td><b>{a['score']}</b></td></tr>"
        for a in alerts)
    img = f'<img src="cid:{map_cid}" alt="Map of today\'s alerts" width="560">' if map_cid else ""
    body = f"""<div style="font-family:system-ui,sans-serif;color:#111">
<h2 style="margin:0 0 4px">{html.escape(subject)}</h2>{img}
<table cellpadding="6" style="border-collapse:collapse;font-size:14px;margin-top:12px">
<tr style="background:#f1f5f3;text-align:left"><th>Level</th><th>Alert</th><th>ha</th><th>Place</th><th>Forest reserve</th><th>River</th><th>Score</th></tr>
{rows}</table>
<p style="color:#555;font-size:12px">Every alert must be reviewed by a person before it is treated as fact.</p></div>"""
    return subject, text, body


def weekly_digest(watch: list[dict], stats: dict, week: str) -> tuple[str, str, str]:
    subject = f"Galamsey Watch weekly digest — week of {week}"
    lines = [subject, "",
             f"New alerts: {stats.get('alerts', 0)}  ·  New hectares: {stats.get('hectares', 0):.1f}",
             f"Confirmed: {stats.get('confirmed', 0)}  ·  Rejected: {stats.get('rejected', 0)}", "",
             f"Watch-level alerts ({len(watch)}):"] + [alert_line(a) for a in watch[:50]]
    text = "\n".join(lines)
    body = "<pre style='font-family:system-ui,sans-serif;white-space:pre-wrap'>" + html.escape(text) + "</pre>"
    return subject, text, body
