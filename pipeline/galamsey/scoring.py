"""Alert scoring (spec 5.6): a 0–100 score plus a level, with a breakdown that the
dashboard shows so an analyst can see *why* an alert scored what it did.

The weights live in the `settings` table (key 'score_weights') so admins can tune
them without a code change; DEFAULT_WEIGHTS is the fallback.
"""

from __future__ import annotations

from dataclasses import dataclass, field

DEFAULT_WEIGHTS = {
    "base": 20,               # any detection that survived the size filter
    "per_extra_source": 15,   # each additional *independent* source family agreeing
    "max_source_bonus": 30,
    "ai": 25,                 # multiplied by the classifier probability (0–1)
    "size_small": 5,          # ≥ 0.3 ha
    "size_medium": 10,        # ≥ 1 ha
    "size_large": 15,         # ≥ 5 ha
    "persistence": 10,        # seen again on a later pass
    "sensitive_zone": 20,     # inside a forest reserve or within 100 m of a major river
    "river_500m": 8,          # within 500 m (only if not already 'sensitive')
    "outside_concession": 10,
    "in_concession": -30,     # still logged, just ranked lower
    "low_confidence": -10,    # cloud-affected, or a single low-confidence source
}

DEFAULT_THRESHOLDS = {"red": 75, "amber": 50, "watch": 30}

# Sources that use the same satellite count once: two optical Sentinel-2 detectors
# agreeing is weaker evidence than optical + radar agreeing.
SOURCE_FAMILY = {
    "gfw_glad_l": "landsat",
    "gfw_glad_s2": "sentinel2", "s2_change": "sentinel2", "planet_change": "planet",
    "gfw_radd": "sentinel1", "s1_change": "sentinel1",
    "gfw_dist": "hls",
    "ai_classifier": "ai",
    "gov_forestry": "government", "gov_cersgis": "government", "gov_equipment": "government",
}


def independent_families(sources: list[str]) -> set[str]:
    return {SOURCE_FAMILY.get(s, s) for s in sources if SOURCE_FAMILY.get(s, s) != "ai"}


@dataclass
class AlertFacts:
    sources: list[str]
    area_ha: float
    max_confidence: float = 0.5           # 0–1, best confidence among detections
    ai_probability: float | None = None
    persistent: bool = False              # detected on ≥ 2 distinct image dates
    in_forest_reserve: bool = False
    river_distance_m: float | None = None
    in_concession: bool | None = None     # None = no concession data available yet
    cloud_affected: bool = False


@dataclass
class Score:
    score: int
    level: str | None                     # 'red' | 'amber' | 'watch' | None (not an alert)
    breakdown: dict[str, int] = field(default_factory=dict)


def score_alert(f: AlertFacts, weights: dict | None = None, thresholds: dict | None = None) -> Score:
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    t = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    b: dict[str, int] = {"base": w["base"]}

    families = independent_families(f.sources)
    if len(families) > 1:
        b["sources_agree"] = min(w["max_source_bonus"], w["per_extra_source"] * (len(families) - 1))

    if f.ai_probability is not None:
        b["ai_classifier"] = round(w["ai"] * max(0.0, min(1.0, f.ai_probability)))

    if f.area_ha >= 5:
        b["size"] = w["size_large"]
    elif f.area_ha >= 1:
        b["size"] = w["size_medium"]
    elif f.area_ha >= 0.3:
        b["size"] = w["size_small"]

    if f.persistent:
        b["persistence"] = w["persistence"]

    near_river = f.river_distance_m is not None and f.river_distance_m <= 100
    sensitive = f.in_forest_reserve or near_river
    if sensitive:
        b["forest_reserve_or_river"] = w["sensitive_zone"]
    elif f.river_distance_m is not None and f.river_distance_m <= 500:
        b["river_500m"] = w["river_500m"]

    if f.in_concession is True:
        b["inside_concession"] = w["in_concession"]
    elif f.in_concession is False:
        b["outside_concession"] = w["outside_concession"]

    if f.cloud_affected or (len(families) <= 1 and f.max_confidence < 0.5 and f.ai_probability is None):
        b["low_confidence"] = w["low_confidence"]

    score = max(0, min(100, sum(b.values())))

    if score >= t["red"] and sensitive:
        level = "red"
    elif score >= t["amber"] and f.in_concession is not True:
        level = "amber"
    elif score >= t["watch"]:
        level = "watch"
    else:
        level = None
    return Score(score=score, level=level, breakdown=b)


def apply_daily_cap(alerts: list[dict], cap: int) -> list[dict]:
    """Avoid alert overload: per region, only the top `cap` red/amber alerts are sent today.

    Each alert dict needs 'region', 'level', 'score'. Returns the same list with
    'digest_only' = True on the ones held back (they still appear in the dashboard
    and the weekly digest).
    """
    by_region: dict[str, list[dict]] = {}
    for a in alerts:
        a["digest_only"] = a["level"] not in ("red", "amber")
        if not a["digest_only"]:
            by_region.setdefault(a.get("region") or "Unknown", []).append(a)
    for group in by_region.values():
        group.sort(key=lambda a: a["score"], reverse=True)
        for a in group[cap:]:
            a["digest_only"] = True
    return alerts
