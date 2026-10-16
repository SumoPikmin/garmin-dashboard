"""All derived numbers: VO2max estimates, race predictions, training load, recovery, volume, insights.

Everything works from the local DB, so it runs offline and on the demo data too.
"""
from __future__ import annotations

import json
import math
import statistics as st
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from . import db
from .config import load_settings

RACES = {"5K": 5000.0, "10K": 10000.0, "Half": 21097.5, "Marathon": 42195.0}
GARMIN_RACE_KEYS = {"5K": "time5K", "10K": "time10K", "Half": "timeHalfMarathon", "Marathon": "timeMarathon"}
SPLIT_KEYS = {1000: "1K", 1609: "Mile", 5000: "5K", 10000: "10K", 21098: "Half", 42195: "Marathon"}
PR_TYPES = {1: "1K", 2: "Mile", 3: "5K", 4: "10K", 5: "Half", 6: "Marathon", 7: "Longest run"}


# ---------------------------------------------------------------- formatting
def fmt_time(sec: float | None) -> str | None:
    if sec is None or not math.isfinite(sec):
        return None
    sec = int(round(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def fmt_pace(sec_per_km: float | None) -> str | None:
    if not sec_per_km or not math.isfinite(sec_per_km):
        return None
    m, s = divmod(int(round(sec_per_km)), 60)
    return f"{m}:{s:02d}/km"


def parse_time(text: str) -> float | None:
    """'3:29:59' or '45:10' -> seconds."""
    if not text:
        return None
    try:
        parts = [float(p) for p in text.strip().split(":")]
    except ValueError:
        return None
    sec = 0.0
    for p in parts:
        sec = sec * 60 + p
    return sec


def sport_of(type_key: str | None) -> str:
    t = (type_key or "").lower()
    if "run" in t:
        return "run"
    if "cycl" in t or "bik" in t or "ride" in t:
        return "bike"
    if "swim" in t:
        return "swim"
    if "strength" in t or "hiit" in t or "fitness" in t or "cardio" in t:
        return "strength"
    if "walk" in t or "hik" in t:
        return "walk"
    return "other"


def _f(v: Any) -> float | None:
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def _first_value(d: Any) -> dict:
    """Garmin often nests per-device dicts: {"<deviceId>": {...}}; take the primary one."""
    if not isinstance(d, dict) or not d:
        return {}
    vals = [v for v in d.values() if isinstance(v, dict)]
    for v in vals:
        if v.get("primaryTrainingDevice"):
            return v
    return vals[0] if vals else {}


# ---------------------------------------------------------------- physiology formulas
def daniels_vo2(v_m_min: float) -> float:
    """Oxygen cost of running at velocity v (m/min), Daniels & Gilbert."""
    return -4.60 + 0.182258 * v_m_min + 0.000104 * v_m_min ** 2


def daniels_pct(t_min: float) -> float:
    """Fraction of VO2max sustainable for t minutes."""
    return 0.8 + 0.1894393 * math.exp(-0.012778 * t_min) + 0.2989558 * math.exp(-0.1932605 * t_min)


def vdot(distance_m: float, time_s: float) -> float | None:
    if not distance_m or not time_s or time_s <= 0:
        return None
    t = time_s / 60
    return daniels_vo2(distance_m / t) / daniels_pct(t)


def time_for_vdot(v: float, distance_m: float) -> float:
    lo, hi = 60.0, 12 * 3600.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if vdot(distance_m, mid) > v:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def riegel(t1: float, d1: float, d2: float, k: float = 1.06) -> float:
    return t1 * (d2 / d1) ** k


def tanda_marathon(weekly_km: float, mean_pace_s_km: float) -> float:
    """Tanda (2011): marathon pace (s/km) from 8-week mean weekly km and mean training pace."""
    pace = 17.1 + 140.0 * math.exp(-0.0053 * weekly_km) + 0.55 * mean_pace_s_km
    return pace * 42.195


def banister_trimp(duration_s: float, avg_hr: float, rest: float, maxhr: float, sex: str) -> float:
    hrr = max(0.0, min(1.0, (avg_hr - rest) / max(1.0, maxhr - rest)))
    a, b = (0.86, 1.67) if sex == "female" else (0.64, 1.92)
    return duration_s / 60 * hrr * a * math.exp(b * hrr)


# ---------------------------------------------------------------- data access
class Data:
    """Everything the analytics need, loaded once from SQLite."""

    def __init__(self, conn, today: date | None = None):
        self.conn = conn
        self.settings = load_settings()
        self.today = today or date.today()
        self.activities = []
        for r in conn.execute("SELECT * FROM activities ORDER BY start_local"):
            a = dict(r)
            a["raw"] = json.loads(a.pop("data"))
            a["sport"] = sport_of(a["type"])
            self.activities.append(a)
        self.daily = defaultdict(dict)
        for r in conn.execute("SELECT date, kind, data FROM daily ORDER BY date"):
            self.daily[r["kind"]][r["date"]] = json.loads(r["data"])
        self.snap = {r["kind"]: json.loads(r["data"]) for r in conn.execute("SELECT kind, data FROM snapshots")}
        self.last_sync = db.last_sync(conn)
        self._resolve_hr()

    def sd(self, kind: str) -> dict:
        """Snapshot as a dict ({} if missing or an unexpected shape)."""
        v = self.snap.get(kind)
        return v if isinstance(v, dict) else {}

    def sl(self, kind: str) -> list:
        v = self.snap.get(kind)
        return v if isinstance(v, list) else []

    @property
    def runs(self) -> list[dict]:
        return [a for a in self.activities if a["sport"] == "run" and (a["distance_m"] or 0) > 0]

    def since(self, days: int, sport: str | None = None) -> list[dict]:
        cutoff = (self.today - timedelta(days=days)).isoformat()
        return [a for a in self.activities if a["date"] > cutoff and (sport is None or a["sport"] == sport)]

    def series(self, kind: str, fn, days: int | None = None) -> list[dict]:
        cutoff = (self.today - timedelta(days=days)).isoformat() if days else ""
        out = []
        for d, v in self.daily.get(kind, {}).items():
            if d >= cutoff:
                x = fn(v)
                if x is not None:
                    out.append({"date": d, "value": x})
        return out

    def _resolve_hr(self) -> None:
        s = self.settings
        user = self.sd("profile").get("userData") or {}
        rhr = [v.get("value") for v in list(self.daily.get("rhr", {}).values())[-14:] if v.get("value")]
        self.resting_hr = s.get("resting_hr") or (st.median(rhr) if rhr else None) or 55.0
        zones = self.sl("hr_zones")
        zone_max = next((z.get("maxHeartRateUsed") for z in zones if isinstance(z, dict) and z.get("maxHeartRateUsed")), None)
        observed = max((a["max_hr"] or 0 for a in self.since(365)), default=0) or None
        age = None
        if user.get("birthDate"):
            try:
                age = (self.today - date.fromisoformat(user["birthDate"][:10])).days / 365.25
            except ValueError:
                pass
        self.max_hr = s.get("max_hr") or zone_max or observed or (round(208 - 0.7 * age) if age else 190.0)
        self.sex = (user.get("gender") or s.get("sex") or "male").lower()
        self.age = age
        self.zones = self._zone_floors(zones)

    def _zone_floors(self, zones: list) -> list[float]:
        z = next((z for z in zones if isinstance(z, dict) and z.get("sport") in ("RUNNING", "DEFAULT") and z.get("zone1Floor")), None)
        if z:
            return [z.get(f"zone{i}Floor") for i in range(1, 6)]
        m = self.max_hr  # %HRmax fallback, same split Garmin uses by default
        return [round(m * p) for p in (0.5, 0.6, 0.7, 0.8, 0.9)]


# ---------------------------------------------------------------- VO2max
def vo2max_section(d: Data) -> dict:
    garmin = d.series("vo2max", lambda v: _f((v.get("generic") or {}).get("vo2MaxPreciseValue"))
                      or _f((v.get("generic") or {}).get("vo2MaxValue")))
    if not garmin:  # fall back to values stamped on activities
        garmin = [{"date": a["date"], "value": a["vo2max"]} for a in d.runs if a["vo2max"]]
    cycling = d.series("vo2max", lambda v: _f((v.get("cycling") or {}).get("vo2MaxPreciseValue")))

    # HR-based estimate per run: %VO2R ≈ %HRR (Swain), oxygen cost from Daniels.
    hr_points = []
    rest, mx = d.resting_hr, d.max_hr
    for a in d.runs:
        if not (a["avg_hr"] and a["duration_s"] and a["duration_s"] >= 1200 and a["avg_speed"]):
            continue
        hrr = (a["avg_hr"] - rest) / (mx - rest)
        climb = (a["elev_gain_m"] or 0) / (a["distance_m"] / 1000)
        if not (0.55 <= hrr <= 0.97) or climb > 20 or "treadmill" in (a["type"] or ""):
            continue
        vo2 = daniels_vo2(a["avg_speed"] * 60)
        hr_points.append({"date": a["date"], "value": round((vo2 - 3.5) / hrr + 3.5, 1)})
    hr_trend = []
    for i, p in enumerate(hr_points):
        window = [q["value"] for q in hr_points[max(0, i - 7): i + 1]]
        hr_trend.append({"date": p["date"], "value": round(st.median(window), 1)})

    # VDOT from best efforts: rolling 6-week best.
    efforts = best_efforts(d)
    vdot_trend = []
    if efforts:
        start = date.fromisoformat(efforts[0]["date"])
        day = start
        while day <= d.today:
            lo = (day - timedelta(days=42)).isoformat()
            window = [e["vdot"] for e in efforts if lo < e["date"] <= day.isoformat()]
            if window:
                vdot_trend.append({"date": day.isoformat(), "value": round(max(window), 1)})
            day += timedelta(days=7)

    uth = round(15.3 * mx / rest, 1) if rest else None
    current = garmin[-1]["value"] if garmin else None
    ago = _value_at(garmin, d.today - timedelta(days=90))
    return {
        "garmin_current": current,
        "garmin_change_90d": round(current - ago, 1) if current and ago else None,
        "garmin_series": garmin,
        "cycling_series": cycling,
        "hr_estimate_current": hr_trend[-1]["value"] if hr_trend else None,
        "hr_estimate_series": hr_trend,
        "vdot_current": current_vdot(d, efforts)["vdot"] if efforts else None,
        "vdot_series": vdot_trend,
        "uth_estimate": uth,
        "fitness_age": d.sd("fitness_age").get("fitnessAge"),
        "chronological_age": d.sd("fitness_age").get("chronologicalAge") or (round(d.age) if d.age else None),
        "category": _vo2_category(current, d.age, d.sex),
    }


def _value_at(series: list[dict], day: date) -> float | None:
    iso = day.isoformat()
    prior = [p["value"] for p in series if p["date"] <= iso]
    return prior[-1] if prior else None


def _vo2_category(v: float | None, age: float | None, sex: str) -> str | None:
    """Rough Cooper Institute bands (percentiles) for context."""
    if not v:
        return None
    age = age or 35
    # thresholds for Fair / Good / Excellent / Superior, male, by decade; females ~ -7
    base = [(29, (37, 41, 45, 49)), (39, (35, 39, 43, 47)), (49, (33, 37, 41, 45)), (59, (30, 34, 38, 42)), (200, (27, 31, 35, 39))]
    cuts = next(c for a, c in base if age <= a)
    if sex == "female":
        cuts = tuple(c - 7 for c in cuts)
    labels = ["Poor", "Fair", "Good", "Excellent", "Superior"]
    return labels[sum(v >= c for c in cuts)]


# ---------------------------------------------------------------- best efforts & predictions
def best_efforts(d: Data) -> list[dict]:
    """Candidate race-like efforts: Garmin fastest splits (≥ 3 km) or whole runs ≥ 3 km."""
    out = []
    for a in d.runs:
        raw = a["raw"]
        found = False
        for dist, label in SPLIT_KEYS.items():
            t = _f(raw.get(f"fastestSplit_{dist}"))
            if t and dist >= 3000:
                v = vdot(dist, t)
                if v and 20 < v < 90:
                    out.append({"date": a["date"], "distance_m": dist, "time_s": t, "label": label,
                                "vdot": round(v, 1), "activity_id": a["activity_id"], "name": a["name"]})
                    found = True
        t = a["moving_s"] or a["duration_s"]
        if not found and a["distance_m"] >= 3000 and t:
            v = vdot(a["distance_m"], t)
            if v and 20 < v < 90:
                out.append({"date": a["date"], "distance_m": a["distance_m"], "time_s": t,
                            "label": f"{a['distance_m'] / 1000:.1f} km", "vdot": round(v, 1),
                            "activity_id": a["activity_id"], "name": a["name"]})
    return sorted(out, key=lambda e: e["date"])


def current_vdot(d: Data, efforts: list[dict], days: int = 42) -> dict:
    cutoff = (d.today - timedelta(days=days)).isoformat()
    recent = [e for e in efforts if e["date"] > cutoff] or efforts[-10:]
    best = max(recent, key=lambda e: e["vdot"])
    return best


def training_volume_stats(d: Data, weeks: int = 8) -> dict:
    runs = d.since(weeks * 7, "run")
    km = sum((a["distance_m"] or 0) for a in runs) / 1000
    secs = sum((a["moving_s"] or a["duration_s"] or 0) for a in runs)
    return {
        "weekly_km": km / weeks,
        "mean_pace": secs / km if km else None,
        "long_runs_30k": sum(1 for a in runs if (a["distance_m"] or 0) >= 30000),
        "longest_km": max(((a["distance_m"] or 0) / 1000 for a in runs), default=0),
    }


def predictions_section(d: Data) -> dict:
    efforts = best_efforts(d)
    latest = d.snap.get("race_pred_latest")
    if isinstance(latest, list):
        latest = latest[0] if latest else None
    hist = list(d.daily.get("race_pred", {}).values())
    if not latest and hist:
        latest = hist[-1]
    vol = training_volume_stats(d)
    basis = current_vdot(d, efforts) if efforts else None
    rows = []
    recent_cut = (d.today - timedelta(days=120)).isoformat()
    for race, dist in RACES.items():
        row = {"race": race, "distance_m": dist}
        g = _f((latest or {}).get(GARMIN_RACE_KEYS[race]))
        row["garmin"] = g
        mine = [e for e in efforts if e["label"] == race and e["date"] > recent_cut]
        if mine:
            best = min(mine, key=lambda e: e["time_s"])
            row["actual"], row["actual_fmt"], row["actual_date"] = best["time_s"], fmt_time(best["time_s"]), best["date"]
            if g:
                row["garmin_gap_pct"] = round(100 * (best["time_s"] - g) / g, 1)
        if basis:
            row["vdot"] = time_for_vdot(basis["vdot"], dist)
            row["riegel"] = riegel(basis["time_s"], basis["distance_m"], dist)
        if race == "Marathon" and vol["weekly_km"] > 5 and vol["mean_pace"]:
            row["tanda"] = tanda_marathon(vol["weekly_km"], vol["mean_pace"])
        values = [row[k] for k in ("garmin", "vdot", "riegel", "tanda") if row.get(k)]
        row["consensus"] = st.median(values) if values else None
        for k in ("garmin", "vdot", "riegel", "tanda", "consensus"):
            if row.get(k):
                row[k + "_fmt"] = fmt_time(row[k])
        if row["consensus"]:
            row["pace_fmt"] = fmt_pace(row["consensus"] / (dist / 1000))
        rows.append(row)

    series = {race: [] for race in RACES}
    for item in hist:
        for race, key in GARMIN_RACE_KEYS.items():
            t = _f(item.get(key))
            if t:
                series[race].append({"date": item.get("calendarDate"), "value": t})

    notes = []
    gaps = [r["garmin_gap_pct"] for r in rows if r.get("garmin_gap_pct") is not None]
    if gaps and abs(st.mean(gaps)) >= 3:
        m = st.mean(gaps)
        notes.append(f"Garmin's predictor is {abs(m):.0f}% {'optimistic' if m > 0 else 'pessimistic'} for you: "
                     f"your actual best times over the same distances (last 4 months) are {'slower' if m > 0 else 'faster'} "
                     f"than its predictions. The VDOT/Riegel columns are based on what you actually ran.")
    if vol["weekly_km"] < 50:
        notes.append(f"Marathon predictions from short efforts assume marathon-specific training; "
                     f"you're averaging {vol['weekly_km']:.0f} km/week, so Tanda's volume-based number is the realistic one.")
    return {
        "rows": rows,
        "basis": basis and {**basis, "time_fmt": fmt_time(basis["time_s"])},
        "volume": {**vol, "mean_pace_fmt": fmt_pace(vol["mean_pace"])},
        "garmin_series": series,
        "notes": notes,
    }


# ---------------------------------------------------------------- training load
def daily_loads(d: Data) -> dict[str, float]:
    loads: dict[str, float] = defaultdict(float)
    for a in d.activities:
        if a["avg_hr"] and a["duration_s"]:
            loads[a["date"]] += banister_trimp(a["duration_s"], a["avg_hr"], d.resting_hr, d.max_hr, d.sex)
        elif a["training_load"]:
            loads[a["date"]] += a["training_load"] * 0.5  # EPOC load is ~2x TRIMP for the same session
        elif a["duration_s"]:
            loads[a["date"]] += a["duration_s"] / 60 * 0.6  # easy-effort guess when no HR
    return loads


def load_section(d: Data) -> dict:
    loads = daily_loads(d)
    if not loads:
        return {"series": []}
    day = min(date.fromisoformat(k) for k in loads)
    ctl = atl = 0.0
    k_ctl, k_atl = 1 - math.exp(-1 / 42), 1 - math.exp(-1 / 7)
    series, history = [], []
    while day <= d.today:
        iso = day.isoformat()
        l = loads.get(iso, 0.0)
        tsb = ctl - atl  # form going into the day
        ctl += k_ctl * (l - ctl)
        atl += k_atl * (l - atl)
        history.append(l)
        acute = sum(history[-7:])
        chronic = sum(history[-28:]) / 4
        series.append({"date": iso, "load": round(l, 1), "ctl": round(ctl, 1), "atl": round(atl, 1),
                       "tsb": round(tsb, 1), "acwr": round(acute / chronic, 2) if chronic > 0 else None})
        day += timedelta(days=1)

    last7 = history[-7:]
    mean7 = st.mean(last7)
    sd7 = st.pstdev(last7)
    monotony = mean7 / sd7 if sd7 > 0 else None
    cur = series[-1]
    week_ago = series[-8] if len(series) > 7 else series[0]
    acwr = cur["acwr"]
    return {
        "series": series[-365:],
        "ctl": cur["ctl"], "atl": cur["atl"], "tsb": round(cur["ctl"] - cur["atl"], 1),
        "acwr": acwr,
        "acwr_zone": None if acwr is None else
        ("low" if acwr < 0.8 else "optimal" if acwr <= 1.3 else "caution" if acwr <= 1.5 else "high"),
        "ramp_7d": round(cur["ctl"] - week_ago["ctl"], 1),
        "monotony": round(monotony, 2) if monotony else None,
        "strain": round(sum(last7) * monotony) if monotony else None,
        "form_label": _form_label(cur["ctl"] - cur["atl"], cur["ctl"]),
    }


def _form_label(tsb: float, ctl: float) -> str:
    rel = tsb / ctl if ctl else 0
    if rel > 0.25:
        return "Fresh — detraining if it lasts"
    if rel > 0.05:
        return "Fresh — race ready"
    if rel > -0.10:
        return "Neutral"
    if rel > -0.30:
        return "Productive fatigue"
    return "Overreaching — watch recovery"


def garmin_status_section(d: Data) -> dict:
    ts_days = d.daily.get("training_status", {})
    latest = ts_days[max(ts_days)] if ts_days else {}
    status = _first_value((latest.get("mostRecentTrainingStatus") or {}).get("latestTrainingStatusData"))
    acute = status.get("acuteTrainingLoadDTO") or {}
    bal = _first_value((latest.get("mostRecentTrainingLoadBalance") or {}).get("metricsTrainingLoadBalanceDTOMap"))
    phrase = status.get("trainingStatusFeedbackPhrase") or ""
    lt = d.sd("lactate_threshold").get("speed_and_heart_rate") or {}
    user = d.sd("profile").get("userData") or {}
    lt_speed = _f(lt.get("speed")) or _f(user.get("lactateThresholdSpeed"))
    if lt_speed and lt_speed < 1.5:  # Garmin stores this one in 0.1 m/s
        lt_speed *= 10
    lt_hr = _f(lt.get("heartRate")) or _f(user.get("lactateThresholdHeartRate"))
    power = d.sd("lactate_threshold").get("power") or {}

    readiness = []
    for day, v in d.daily.get("readiness", {}).items():
        entries = v if isinstance(v, list) else [v]
        entries = [e for e in entries if isinstance(e, dict)]
        if entries:
            e = next((x for x in entries if x.get("inputContext") == "AFTER_WAKEUP_RESET"), entries[0])
            if e.get("score") is not None:
                readiness.append({"date": day, "value": e.get("score"), "level": e.get("level"),
                                  "feedback": e.get("feedbackShort")})

    endurance = d.sd("endurance_latest")
    end_score = _f(endurance.get("overallScore"))
    hill = list(d.daily.get("hill", {}).values())
    hill_last = hill[-1] if hill else {}

    return {
        "status": phrase.split("_")[0].replace("NO", "No status").title() if phrase else None,
        "status_phrase": phrase,
        "acute_load": acute.get("dailyTrainingLoadAcute"),
        "chronic_load": acute.get("dailyTrainingLoadChronic"),
        "acwr": acute.get("dailyAcuteChronicWorkloadRatio"),
        "acwr_status": acute.get("acwrStatus"),
        "load_tunnel": [status.get("loadTunnelMin"), status.get("loadTunnelMax")],
        "weekly_load": status.get("weeklyTrainingLoad"),
        "load_focus": {
            "aerobic_low": bal.get("monthlyLoadAerobicLow"),
            "aerobic_high": bal.get("monthlyLoadAerobicHigh"),
            "anaerobic": bal.get("monthlyLoadAnaerobic"),
            "targets": {
                "aerobic_low": [bal.get("monthlyLoadAerobicLowTargetMin"), bal.get("monthlyLoadAerobicLowTargetMax")],
                "aerobic_high": [bal.get("monthlyLoadAerobicHighTargetMin"), bal.get("monthlyLoadAerobicHighTargetMax")],
                "anaerobic": [bal.get("monthlyLoadAnaerobicTargetMin"), bal.get("monthlyLoadAnaerobicTargetMax")],
            },
            "feedback": bal.get("trainingBalanceFeedbackPhrase"),
        },
        "readiness_current": readiness[-1] if readiness else None,
        "readiness_series": readiness,
        "lactate_threshold": {"hr": lt_hr, "pace_fmt": fmt_pace(1000 / lt_speed) if lt_speed else None,
                              "speed": lt_speed},
        "endurance_score": end_score,
        "endurance_class": _endurance_class(endurance, end_score),
        "endurance_series": _endurance_series(d.sd("endurance_range")),
        "running_ftp": _f(power.get("functionalThresholdPower")),
        "power_to_weight": round(_f(power.get("powerToWeight")), 2) if _f(power.get("powerToWeight")) else None,
        "hill_score": hill_last.get("overallScore"),
        "hill_strength": hill_last.get("strengthScore"),
        "hill_endurance": hill_last.get("enduranceScore"),
    }


def _endurance_class(e: dict, score: float | None) -> str | None:
    if score is None:
        return None
    limits = {k.replace("classificationLowerLimit", ""): v for k, v in e.items()
              if k.startswith("classificationLowerLimit") and isinstance(v, (int, float))}
    passed = [name for name, lim in sorted(limits.items(), key=lambda kv: kv[1]) if score >= lim]
    if passed:
        return passed[-1].replace("Elite", "Elite").replace("WellTrained", "Well-trained")
    return "Recreational" if limits else None


def _endurance_series(r: dict) -> list[dict]:
    out = []
    for day, g in sorted((r.get("groupMap") or {}).items()):
        v = _f((g or {}).get("groupAverage"))
        if v:
            out.append({"date": day, "value": v})
    return out


# ---------------------------------------------------------------- recovery
def recovery_section(d: Data) -> dict:
    hrv = d.series("hrv", lambda v: _f(v.get("lastNightAvg")))
    hrv_week = d.series("hrv", lambda v: _f(v.get("weeklyAvg")))
    band = d.series("hrv", lambda v: [(v.get("baseline") or {}).get("balancedLow"),
                                      (v.get("baseline") or {}).get("balancedUpper")]
                    if (v.get("baseline") or {}).get("balancedLow") else None)
    hrv_status = list(d.daily.get("hrv", {}).values())[-1].get("status") if d.daily.get("hrv") else None
    rhr = d.series("rhr", lambda v: _f(v.get("value")))
    sleep_rows = d.daily.get("sleep", {})
    sleep = []
    for day, v in sleep_rows.items():
        vals = v.get("values") or v
        dur = _f(vals.get("totalSleepTimeInSeconds"))
        if dur:
            sleep.append({"date": day, "hours": round(dur / 3600, 2), "score": _f(vals.get("sleepScore")),
                          "deep": _f(vals.get("deepTime")), "rem": _f(vals.get("remTime")),
                          "light": _f(vals.get("lightTime")), "awake": _f(vals.get("awakeTime"))})
    bb = d.series("body_battery", lambda v: [v.get("min"), v.get("max")] if v.get("max") is not None else None, 400)
    steps = d.series("steps", lambda v: _f(v.get("totalSteps")), 400)
    stress = [{"date": x.get("calendarDate"), "value": x.get("value")}
              for x in d.sl("weekly_stress") if isinstance(x, dict) and x.get("value") is not None]
    weight = d.series("weight", lambda v: round(v["weight"] / 1000, 1) if _f(v.get("weight")) else None)

    def z(series, key="value", days=60, last=3):
        vals = [p[key] for p in series[-days:] if p.get(key) is not None]
        if len(vals) < 14:
            return None
        base, sd = st.mean(vals[:-last]), st.pstdev(vals[:-last]) or 1
        return (st.mean(vals[-last:]) - base) / sd

    z_hrv, z_rhr, z_sleep = z(hrv), z(rhr), z(sleep, "hours")
    parts = [x for x in (z_hrv, -z_rhr if z_rhr is not None else None, z_sleep) if x is not None]
    score = round(max(0, min(100, 60 + 15 * st.mean(parts)))) if parts else None
    rhr7 = [p["value"] for p in rhr[-7:]]
    rhr30 = [p["value"] for p in rhr[-37:-7]]
    s7 = sleep[-7:]
    goal = d.settings.get("sleep_goal_h", 8)
    return {
        "score": score,
        "label": None if score is None else ("Primed" if score >= 70 else "Normal" if score >= 45 else "Strained"),
        "components": {"hrv_z": _r(z_hrv), "rhr_z": _r(z_rhr), "sleep_z": _r(z_sleep)},
        "hrv_series": hrv, "hrv_weekly": hrv_week, "hrv_band": band, "hrv_status": hrv_status,
        "hrv_last": hrv[-1]["value"] if hrv else None,
        "rhr_series": rhr,
        "rhr_7d": round(st.mean(rhr7), 1) if rhr7 else None,
        "rhr_delta": round(st.mean(rhr7) - st.mean(rhr30), 1) if rhr7 and rhr30 else None,
        "sleep_series": sleep,
        "sleep_7d_h": round(st.mean(p["hours"] for p in s7), 2) if s7 else None,
        "sleep_7d_score": round(st.mean(p["score"] for p in s7 if p["score"])) if any(p["score"] for p in s7) else None,
        "sleep_debt_7d_h": round(sum(goal - p["hours"] for p in s7), 1) if s7 else None,
        "body_battery": bb, "steps": steps, "weekly_stress": stress, "weight": weight,
    }


def _r(x):
    return round(x, 2) if x is not None else None


# ---------------------------------------------------------------- volume & intensity
def volume_section(d: Data) -> dict:
    weeks: dict[str, dict] = defaultdict(lambda: defaultdict(float))
    days: dict[str, dict] = defaultdict(lambda: {"min": 0.0, "km": 0.0, "n": 0})
    long_run: dict[str, float] = defaultdict(float)
    for a in d.activities:
        dt = date.fromisoformat(a["date"])
        wk = (dt - timedelta(days=dt.weekday())).isoformat()
        km = (a["distance_m"] or 0) / 1000
        hrs = (a["duration_s"] or 0) / 3600
        weeks[wk][a["sport"] + "_km"] += km
        weeks[wk][a["sport"] + "_h"] += hrs
        weeks[wk]["total_h"] += hrs
        weeks[wk]["count"] += 1
        weeks[wk]["elev"] += a["elev_gain_m"] or 0
        if a["sport"] == "run":
            long_run[wk] = max(long_run[wk], km)
        days[a["date"]]["min"] += hrs * 60
        days[a["date"]]["km"] += km
        days[a["date"]]["n"] += 1

    first_week = d.today - timedelta(days=d.today.weekday() + 7 * 51)
    weekly = []
    for i in range(52):
        wk = (first_week + timedelta(weeks=i)).isoformat()
        w = weeks.get(wk, {})
        weekly.append({"week": wk, **{k: round(v, 2) for k, v in w.items()}, "long_run_km": round(long_run.get(wk, 0), 1)})

    heat = []
    for i in range(371):
        day = (d.today - timedelta(days=370 - i)).isoformat()
        x = days.get(day)
        heat.append({"date": day, "min": round(x["min"]) if x else 0, "km": round(x["km"], 1) if x else 0})

    def totals(start: date, end: date) -> dict:
        out = defaultdict(float)
        for a in d.activities:
            if start.isoformat() <= a["date"] <= end.isoformat():
                out[a["sport"] + "_km"] += (a["distance_m"] or 0) / 1000
                out["hours"] += (a["duration_s"] or 0) / 3600
                out["count"] += 1
                out["elev"] += a["elev_gain_m"] or 0
        return {k: round(v, 1) for k, v in out.items()}

    y0 = date(d.today.year, 1, 1)
    ly_today = d.today.replace(year=d.today.year - 1) if not (d.today.month == 2 and d.today.day == 29) else d.today - timedelta(days=366)
    runs_4w = [w.get("run_km", 0) for w in weekly[-4:]]
    return {
        "weekly": weekly,
        "heatmap": heat,
        "ytd": totals(y0, d.today),
        # only comparable when the history actually covers last year's January
        "last_year_ytd": totals(date(d.today.year - 1, 1, 1), ly_today)
        if d.activities and d.activities[0]["date"] <= f"{d.today.year - 1}-01-15" else {},
        "last_7d": totals(d.today - timedelta(days=6), d.today),
        "last_28d": totals(d.today - timedelta(days=27), d.today),
        "run_km_4w_avg": round(st.mean(runs_4w), 1),
        "streak_days": _streak(days, d.today),
        "sports": sorted({a["sport"] for a in d.activities}),
    }


def _streak(days: dict, today: date) -> int:
    n, day = 0, today
    if today.isoformat() not in days:
        day -= timedelta(days=1)
    while day.isoformat() in days:
        n += 1
        day -= timedelta(days=1)
    return n


def intensity_section(d: Data, days: int = 28) -> dict:
    zones = [0.0] * 5
    measured = estimated = 0.0
    for a in d.since(days):
        raw = a["raw"]
        z = [_f(raw.get(f"hrTimeInZone_{i}")) for i in range(1, 6)]
        if any(z):
            measured += a["duration_s"] or 0
            for i, v in enumerate(z):
                zones[i] += v or 0
        elif a["avg_hr"] and a["duration_s"]:
            estimated += a["duration_s"]
            idx = max([i for i, floor in enumerate(d.zones) if floor and a["avg_hr"] >= floor] or [0])
            zones[idx] += a["duration_s"]
    total = sum(zones)
    source = "garmin" if measured >= 3 * estimated else "estimated from average HR"
    pct = [round(100 * z / total, 1) if total else 0 for z in zones]
    easy = pct[0] + pct[1]
    hard = pct[3] + pct[4]
    if not total:
        verdict = None
    elif easy >= 75 and hard >= 8:
        verdict = "Polarized — textbook 80/20"
    elif easy >= 75:
        verdict = "Mostly easy — room for one quality session a week"
    elif pct[2] > 25:
        verdict = "Lots of Zone 3 ('grey zone') — make easy days easier"
    else:
        verdict = "High-intensity heavy — watch recovery"
    return {"minutes": [round(z / 60) for z in zones], "pct": pct, "easy_pct": round(easy, 1),
            "moderate_pct": pct[2], "hard_pct": round(hard, 1), "verdict": verdict, "source": source,
            "zone_floors": d.zones, "max_hr": d.max_hr, "resting_hr": d.resting_hr}


def efficiency_section(d: Data) -> dict:
    """Efficiency factor (m/min per bpm) and cadence trends for steady runs."""
    pts = []
    for a in d.runs:
        if a["avg_hr"] and a["avg_speed"] and (a["duration_s"] or 0) >= 1200:
            pts.append({"date": a["date"], "ef": round(a["avg_speed"] * 60 / a["avg_hr"], 3),
                        "pace": round(1000 / a["avg_speed"]), "hr": a["avg_hr"],
                        "cadence": a["avg_cadence"], "stride_cm": _f(a["raw"].get("avgStrideLength"))})
    trend = []
    for i, p in enumerate(pts):
        lo = (date.fromisoformat(p["date"]) - timedelta(days=28)).isoformat()
        w = [q["ef"] for q in pts[: i + 1] if q["date"] > lo]
        trend.append({"date": p["date"], "value": round(st.mean(w), 3)})

    def avg_ef(days_from: int, days_to: int):
        lo = (d.today - timedelta(days=days_from)).isoformat()
        hi = (d.today - timedelta(days=days_to)).isoformat()
        w = [p["ef"] for p in pts if lo < p["date"] <= hi]
        return st.mean(w) if w else None
    now, before = avg_ef(28, 0), avg_ef(118, 90)
    # Aerobic pace: average pace of runs in Zone 2
    z2_lo, z2_hi = d.zones[1], d.zones[2]
    z2 = [p["pace"] for p in pts[-40:] if z2_lo and z2_hi and z2_lo <= p["hr"] < z2_hi]
    cad = [p["cadence"] for p in pts[-20:] if p["cadence"]]
    return {
        "points": pts[-200:], "trend": trend[-200:],
        "ef_change_pct": round(100 * (now - before) / before, 1) if now and before else None,
        "zone2_pace_fmt": fmt_pace(st.mean(z2)) if z2 else None,
        "cadence_avg": round(st.mean(cad)) if cad else None,
    }


# ---------------------------------------------------------------- records, activities, goal
def records_section(d: Data) -> dict:
    garmin = []
    for pr in d.sl("personal_records"):
        if not isinstance(pr, dict):
            continue
        tid = pr.get("typeId")
        if tid not in PR_TYPES:
            continue
        val = _f(pr.get("value"))
        garmin.append({
            "label": PR_TYPES[tid],
            "value_fmt": f"{val / 1000:.2f} km" if tid == 7 else fmt_time(val),
            "date": (pr.get("actStartDateTimeInGMTFormatted") or pr.get("prStartTimeGmtFormatted") or "")[:10],
            "activity": pr.get("activityName"),
        })
    efforts = best_efforts(d)
    computed = {}
    year = str(d.today.year)
    for e in efforts:
        if e["label"] not in SPLIT_KEYS.values():
            continue
        for scope in ("all", year):
            if scope != "all" and not e["date"].startswith(year):
                continue
            key = (scope, e["label"])
            if key not in computed or e["time_s"] < computed[key]["time_s"]:
                computed[key] = e
    bests = []
    for label in SPLIT_KEYS.values():
        a, y = computed.get(("all", label)), computed.get((year, label))
        if a:
            bests.append({"label": label, "all_time": fmt_time(a["time_s"]), "all_time_date": a["date"],
                          "this_year": fmt_time(y["time_s"]) if y else None, "vdot": a["vdot"]})
    longest = max(d.runs, key=lambda a: a["distance_m"], default=None)
    biggest_week = max(volume_section(d)["weekly"], key=lambda w: w.get("run_km", 0), default=None)
    return {"garmin": garmin, "bests": bests,
            "longest_run_km": round(longest["distance_m"] / 1000, 1) if longest else None,
            "longest_run_date": longest["date"] if longest else None,
            "biggest_run_week_km": biggest_week.get("run_km") if biggest_week else None,
            "biggest_run_week": biggest_week["week"] if biggest_week else None}


def activities_list(d: Data, limit: int = 40, sport: str | None = None) -> list[dict]:
    acts = [a for a in d.activities if sport is None or a["sport"] == sport]
    out = []
    for a in reversed(acts[-limit:]):
        km = (a["distance_m"] or 0) / 1000
        dur = a["moving_s"] or a["duration_s"] or 0
        out.append({
            "id": a["activity_id"], "date": a["date"], "start": a["start_local"][11:16], "name": a["name"],
            "type": a["type"], "sport": a["sport"], "km": round(km, 2), "duration_fmt": fmt_time(a["duration_s"]),
            "pace_fmt": fmt_pace(dur / km) if a["sport"] == "run" and km > 0.2 else None,
            "speed_kmh": round(km / (dur / 3600), 1) if a["sport"] == "bike" and dur else None,
            "avg_hr": a["avg_hr"], "elev": a["elev_gain_m"], "load": a["training_load"],
            "te": a["aerobic_te"], "ate": a["anaerobic_te"], "calories": a["calories"],
            "trimp": round(banister_trimp(a["duration_s"], a["avg_hr"], d.resting_hr, d.max_hr, d.sex))
            if a["avg_hr"] and a["duration_s"] else None,
            "label": a["raw"].get("trainingEffectLabel"),
        })
    return out


def goal_section(d: Data, predictions: dict) -> dict | None:
    g = d.settings.get("goal_race") or {}
    if not g.get("name") or not g.get("date"):
        return None
    race_day = date.fromisoformat(g["date"])
    dist = float(g.get("distance_km") or 42.195) * 1000
    target = parse_time(g.get("target_time", ""))
    basis = predictions.get("basis")
    predicted = None
    if basis:
        predicted = riegel(basis["time_s"], basis["distance_m"], dist)
        vt = time_for_vdot(basis["vdot"], dist)
        predicted = st.median([predicted, vt])
    match = next((r for r in predictions["rows"] if abs(r["distance_m"] - dist) < 300), None)
    if match and match.get("consensus"):
        predicted = match["consensus"]
    return {
        "name": g["name"], "date": g["date"], "distance_km": dist / 1000,
        "days_left": (race_day - d.today).days, "weeks_left": round((race_day - d.today).days / 7, 1),
        "target_fmt": fmt_time(target), "target_pace_fmt": fmt_pace(target / (dist / 1000)) if target else None,
        "required_vdot": round(vdot(dist, target), 1) if target else None,
        "current_vdot": basis["vdot"] if basis else None,
        "predicted_fmt": fmt_time(predicted), "predicted_pace_fmt": fmt_pace(predicted / (dist / 1000)) if predicted else None,
        "gap_s": round(predicted - target) if predicted and target else None,
    }


# ---------------------------------------------------------------- insights
def insights(sections: dict) -> list[dict]:
    out = []
    v, l, r, i, p, e = (sections[k] for k in ("vo2max", "load", "recovery", "intensity", "predictions", "efficiency"))

    def add(level, text):
        out.append({"level": level, "text": text})

    if v.get("garmin_change_90d") is not None:
        ch = v["garmin_change_90d"]
        add("good" if ch > 0 else "warn" if ch < -1 else "info",
            f"VO2max {'up' if ch > 0 else 'down' if ch < 0 else 'flat'} {abs(ch):.1f} over 90 days (now {v['garmin_current']:.1f}).")
    if l.get("acwr") is not None:
        z = l["acwr_zone"]
        msg = {"low": "training less than your 4-week norm — fine for a taper, otherwise fitness slips",
               "optimal": "in the sweet spot (0.8–1.3)",
               "caution": "ramping quickly — keep the next few days easy",
               "high": "well above your norm — injury risk is elevated"}[z]
        add({"low": "info", "optimal": "good", "caution": "warn", "high": "bad"}[z], f"Acute:chronic load {l['acwr']:.2f} — {msg}.")
    if l.get("monotony") and l["monotony"] > 2:
        add("warn", f"Training monotony {l['monotony']:.1f} (>2): days are too similar; add a real rest day or an easier day.")
    if r.get("rhr_delta") is not None and r["rhr_delta"] >= 3:
        add("warn", f"Resting HR is {r['rhr_delta']:+.1f} bpm vs your 30-day baseline — possible fatigue, illness or stress.")
    if r.get("sleep_7d_h") is not None and r["sleep_7d_h"] < 7:
        add("warn", f"Averaging {r['sleep_7d_h']:.1f} h sleep this week — {r['sleep_debt_7d_h']:.1f} h short of your goal over 7 nights.")
    if r.get("components", {}).get("hrv_z") is not None and r["components"]["hrv_z"] < -1:
        add("warn", "HRV the last 3 nights is clearly below your baseline.")
    if i.get("verdict"):
        add("info" if i["easy_pct"] >= 75 else "warn", f"Last 4 weeks: {i['easy_pct']:.0f}% easy / {i['moderate_pct']:.0f}% moderate / {i['hard_pct']:.0f}% hard — {i['verdict']}.")
    if e.get("ef_change_pct") is not None:
        ch = e["ef_change_pct"]
        add("good" if ch > 1 else "warn" if ch < -2 else "info",
            f"Aerobic efficiency {ch:+.1f}% vs 3 months ago (more speed per heartbeat is better).")
    for n in p.get("notes", []):
        add("info", n)
    return out


# ---------------------------------------------------------------- top level
def build_dashboard(conn, today: date | None = None) -> dict:
    d = Data(conn, today)
    errors: dict[str, str] = {}

    def safe(name, fn, default):
        try:
            return fn()
        except Exception as e:  # one odd Garmin payload must not take the whole dashboard down
            errors[name] = f"{type(e).__name__}: {e}"
            return default

    empty_series = {"series": []}
    preds = safe("predictions", lambda: predictions_section(d),
                 {"rows": [], "basis": None, "volume": {}, "garmin_series": {r: [] for r in RACES}, "notes": []})
    sections = {
        "vo2max": safe("vo2max", lambda: vo2max_section(d), {"garmin_series": [], "hr_estimate_series": [], "vdot_series": []}),
        "predictions": preds,
        "load": safe("load", lambda: load_section(d), empty_series),
        "garmin_status": safe("garmin_status", lambda: garmin_status_section(d), {"readiness_series": [], "load_focus": {}}),
        "recovery": safe("recovery", lambda: recovery_section(d), {}),
        "volume": safe("volume", lambda: volume_section(d), None),
        "intensity": safe("intensity", lambda: intensity_section(d), None),
        "efficiency": safe("efficiency", lambda: efficiency_section(d), {"points": [], "trend": []}),
        "records": safe("records", lambda: records_section(d), {"garmin": [], "bests": []}),
        "activities": safe("activities", lambda: activities_list(d), []),
    }
    sections["goal"] = safe("goal", lambda: goal_section(d, preds), None)
    sections["insights"] = safe("insights", lambda: insights(sections), [])
    profile = d.sd("profile")
    sections["meta"] = {
        "today": d.today.isoformat(),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "last_sync": d.last_sync,
        "activity_count": len(d.activities),
        "first_activity": d.activities[0]["date"] if d.activities else None,
        "name": (d.snap.get("full_name") if isinstance(d.snap.get("full_name"), str) else None) or profile.get("displayName") or profile.get("fullName") or (profile.get("userData") or {}).get("displayName"),
        "max_hr": d.max_hr, "resting_hr": d.resting_hr,
        "demo": bool(d.snap.get("demo")),
        "section_errors": errors,
    }
    return sections
