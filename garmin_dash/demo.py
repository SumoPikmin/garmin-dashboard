"""Synthetic year of marathon training, shaped like Garmin's API payloads. Used for testing (data/demo.sqlite)."""
from __future__ import annotations

import math
import random
from datetime import date, datetime, timedelta

from . import db
from .analytics import time_for_vdot


def _speed_for(vo2: float) -> float:
    """Inverse Daniels: m/s at a given oxygen cost."""
    a, b, c = 0.000104, 0.182258, -4.60 - vo2
    return (-b + math.sqrt(b * b - 4 * a * c)) / (2 * a) / 60


def build(days: int = 365, seed: int = 7) -> None:
    rng = random.Random(seed)
    db.use_demo()
    from .config import DEMO_DB_PATH
    if DEMO_DB_PATH.exists():
        DEMO_DB_PATH.unlink()
    conn = db.connect()
    today = date.today()
    start = today - timedelta(days=days)
    rest_hr, max_hr = 49, 191
    acts, aid = [], 10_000_000_000

    def vdot_on(day: date) -> float:
        t = (day - start).days / days
        return 44.0 + 6.5 * t - (1.2 if 0.45 < t < 0.52 else 0)  # small dip mid-year (holiday)

    for i in range(days + 1):
        day = start + timedelta(days=i)
        t = i / days
        v = vdot_on(day)
        wd = day.weekday()
        weekly_target = 30 + 35 * min(1, t * 1.15)  # 30 -> 65 km/week
        if 0.45 < t < 0.52:
            weekly_target *= 0.4
        plan = {0: None, 1: "intervals", 2: "easy", 3: "tempo", 4: None, 5: "easy", 6: "long"}
        kind = plan[wd]
        if kind and rng.random() < 0.08:
            kind = None  # missed session
        if wd == 0 and rng.random() < 0.6:
            acts.append(_other(day, "strength_training", rng, aid := aid + 1, rest_hr, max_hr))
        if wd == 4 and rng.random() < 0.5:
            acts.append(_other(day, "road_biking", rng, aid := aid + 1, rest_hr, max_hr))
        if not kind:
            continue
        share = {"intervals": 0.17, "easy": 0.2, "tempo": 0.19, "long": 0.34}[kind]
        km = max(5, weekly_target * share * rng.uniform(0.9, 1.1))
        if kind == "long":
            km = min(km, 34)
        frac = {"easy": 0.66, "long": 0.70, "tempo": 0.86, "intervals": 0.82}[kind]
        speed = _speed_for(v * frac) * rng.uniform(0.97, 1.03)
        dist = km * 1000
        dur = dist / speed
        vo2r = (v * frac - 3.5) / (v - 3.5)
        avg_hr = rest_hr + (max_hr - rest_hr) * vo2r * rng.uniform(0.97, 1.04) + (3 if kind == "long" else 0)
        aid += 1
        a = {
            "activityId": aid,
            "activityName": {"easy": "Easy run", "long": "Long run", "tempo": "Tempo run", "intervals": "Intervals 6x1km"}[kind],
            "startTimeLocal": datetime.combine(day, datetime.min.time()).replace(hour=rng.choice([7, 7, 18, 19]), minute=rng.randint(0, 50)).strftime("%Y-%m-%d %H:%M:%S"),
            "activityType": {"typeKey": "running"},
            "distance": round(dist, 1), "duration": round(dur * 1.02, 1), "movingDuration": round(dur, 1),
            "elevationGain": round(km * rng.uniform(3, 12)), "averageHR": round(avg_hr), "maxHR": round(min(max_hr, avg_hr + rng.uniform(8, 22))),
            "averageSpeed": round(speed, 3), "calories": round(km * 68),
            "aerobicTrainingEffect": round(min(5, 2 + frac * 2 + km / 30), 1),
            "anaerobicTrainingEffect": round({"intervals": 2.6, "tempo": 1.2}.get(kind, 0.2) + rng.uniform(0, 0.6), 1),
            "averageRunningCadenceInStepsPerMinute": round(166 + 8 * (frac - 0.66) / 0.2 + rng.uniform(-2, 2)),
            "avgStrideLength": round(speed * 60 / 170 * 100, 1),
            "vO2MaxValue": round(v + 1.5),
            "trainingEffectLabel": {"easy": "AEROBIC_BASE", "long": "AEROBIC_BASE", "tempo": "LACTATE_THRESHOLD", "intervals": "VO2MAX"}[kind],
        }
        a["activityTrainingLoad"] = round(dur / 60 * vo2r * 2.6 * math.exp(1.2 * vo2r), 1)
        zone_mix = {"easy": [0.15, 0.7, 0.13, 0.02, 0], "long": [0.1, 0.6, 0.25, 0.05, 0],
                    "tempo": [0.15, 0.2, 0.2, 0.4, 0.05], "intervals": [0.2, 0.25, 0.15, 0.2, 0.2]}[kind]
        for z, share_z in enumerate(zone_mix, 1):
            a[f"hrTimeInZone_{z}"] = round(dur * share_z)
        for split in (1000, 1609, 5000, 10000, 21098):
            if dist >= split:
                race_v = v * (0.97 if kind in ("tempo", "intervals") else 0.86)
                a[f"fastestSplit_{split}"] = round(time_for_vdot(race_v, split) * rng.uniform(1.0, 1.04), 1)
        acts.append(a)

    # a 10K race and a half marathon to anchor the predictions
    for offset, dist, name in ((120, 10000, "City 10K"), (40, 21097.5, "Half Marathon")):
        day = today - timedelta(days=offset)
        tt = time_for_vdot(vdot_on(day), dist)
        aid += 1
        acts.append({"activityId": aid, "activityName": name, "activityType": {"typeKey": "running"},
                     "startTimeLocal": f"{day.isoformat()} 09:30:00", "distance": dist, "duration": tt + 5, "movingDuration": tt,
                     "elevationGain": 40, "averageHR": 176, "maxHR": 188, "averageSpeed": dist / tt, "calories": dist / 1000 * 70,
                     "aerobicTrainingEffect": 4.8, "anaerobicTrainingEffect": 2.4, "activityTrainingLoad": 280,
                     "averageRunningCadenceInStepsPerMinute": 180, "trainingEffectLabel": "LACTATE_THRESHOLD",
                     f"fastestSplit_{int(dist) if dist == 10000 else 21098}": round(tt, 1),
                     "fastestSplit_5000": round(time_for_vdot(vdot_on(day), 5000) * 1.02, 1),
                     "hrTimeInZone_3": tt * 0.1, "hrTimeInZone_4": tt * 0.6, "hrTimeInZone_5": tt * 0.3})
    db.upsert_activities(conn, acts)

    # ---------- daily wellness ----------
    weight = 72.5
    for i in range(days + 1):
        day = start + timedelta(days=i)
        iso = day.isoformat()
        v = vdot_on(day)
        late = i > days - 6
        hrv = 58 + 10 * i / days + rng.gauss(0, 5) - (9 if late and i % 2 else 0)
        db.put_daily(conn, iso, "vo2max", {"generic": {"calendarDate": iso, "vo2MaxPreciseValue": round(v + 1.6 + rng.gauss(0, 0.15), 1), "vo2MaxValue": round(v + 1.6)}})
        db.put_daily(conn, iso, "race_pred", {"calendarDate": iso, **{k: round(time_for_vdot(v + 1.2, dd) * f) for k, dd, f in (
            ("time5K", 5000, 1.0), ("time10K", 10000, 1.0), ("timeHalfMarathon", 21097.5, 1.01), ("timeMarathon", 42195, 1.03))}})
        db.put_daily(conn, iso, "hrv", {"calendarDate": iso, "lastNightAvg": round(hrv), "weeklyAvg": round(58 + 10 * i / days),
                                        "baseline": {"balancedLow": round(52 + 9 * i / days), "balancedUpper": round(66 + 10 * i / days)},
                                        "status": "UNBALANCED" if late else "BALANCED"})
        db.put_daily(conn, iso, "rhr", {"calendarDate": iso, "value": round(52 - 4 * i / days + rng.gauss(0, 1.3) + (3 if late else 0))})
        hours = max(4.5, rng.gauss(7.4, 0.7) - (0.8 if late else 0))
        db.put_daily(conn, iso, "sleep", {"calendarDate": iso, "values": {
            "totalSleepTimeInSeconds": round(hours * 3600), "sleepScore": round(min(98, 45 + hours * 5 + rng.gauss(0, 5))),
            "deepTime": round(hours * 3600 * 0.18), "remTime": round(hours * 3600 * 0.22), "lightTime": round(hours * 3600 * 0.55),
            "awakeTime": round(hours * 3600 * 0.05)}})
        db.put_daily(conn, iso, "steps", {"calendarDate": iso, "totalSteps": round(rng.gauss(11500, 3000)), "stepGoal": 10000})
        if i > days - 180:
            lo = rng.randint(8, 30)
            db.put_daily(conn, iso, "body_battery", {"charged": 60, "drained": 62, "min": lo, "max": min(100, lo + rng.randint(50, 75)), "end": lo + 10})
        if i % 3 == 0:
            weight += rng.gauss(-0.01, 0.15)
            db.put_daily(conn, iso, "weight", {"calendarDate": iso, "weight": round(weight * 1000), "bodyFat": 14.5})
        if i > days - 90:
            score = max(5, min(100, round(70 + (hrv - 63) * 1.5 + rng.gauss(0, 8))))
            db.put_daily(conn, iso, "readiness", [{"calendarDate": iso, "score": score, "inputContext": "AFTER_WAKEUP_RESET",
                                                    "level": "HIGH" if score > 75 else "MODERATE" if score > 50 else "LOW",
                                                    "feedbackShort": "WELL_RECOVERED" if score > 75 else "RECOVERING"}])
            db.put_daily(conn, iso, "hill", {"calendarDate": iso, "overallScore": 62 + round(8 * i / days), "strengthScore": 21, "enduranceScore": 48})
        if i > days - 42:
            db.put_daily(conn, iso, "training_status", {
                "mostRecentTrainingStatus": {"latestTrainingStatusData": {"123": {
                    "calendarDate": iso, "primaryTrainingDevice": True, "trainingStatusFeedbackPhrase": "PRODUCTIVE_3",
                    "weeklyTrainingLoad": 690, "loadTunnelMin": 420, "loadTunnelMax": 780,
                    "acuteTrainingLoadDTO": {"dailyTrainingLoadAcute": 690, "dailyTrainingLoadChronic": 560,
                                             "dailyAcuteChronicWorkloadRatio": 1.2, "acwrStatus": "OPTIMAL"}}}},
                "mostRecentTrainingLoadBalance": {"metricsTrainingLoadBalanceDTOMap": {"123": {
                    "primaryTrainingDevice": True, "monthlyLoadAerobicLow": 1250, "monthlyLoadAerobicHigh": 980, "monthlyLoadAnaerobic": 210,
                    "monthlyLoadAerobicLowTargetMin": 900, "monthlyLoadAerobicLowTargetMax": 1500,
                    "monthlyLoadAerobicHighTargetMin": 700, "monthlyLoadAerobicHighTargetMax": 1200,
                    "monthlyLoadAnaerobicTargetMin": 250, "monthlyLoadAnaerobicTargetMax": 600,
                    "trainingBalanceFeedbackPhrase": "ANAEROBIC_SHORTAGE"}}}})
        if i > days - 30:
            db.put_daily(conn, iso, "stats", {"totalSteps": 11000, "restingHeartRate": 49})

    v_now = vdot_on(today)
    db.put_snapshot(conn, "demo", True)
    db.put_snapshot(conn, "profile", {"displayName": "Demo Runner", "userData": {"gender": "MALE", "birthDate": "1993-04-12",
                                                                                 "lactateThresholdSpeed": 0.372, "lactateThresholdHeartRate": 172}})
    db.put_snapshot(conn, "race_pred_latest", {k: round(time_for_vdot(v_now + 1.2, dd) * f) for k, dd, f in (
        ("time5K", 5000, 1.0), ("time10K", 10000, 1.0), ("timeHalfMarathon", 21097.5, 1.01), ("timeMarathon", 42195, 1.03))})
    db.put_snapshot(conn, "hr_zones", [{"sport": "DEFAULT", "trainingMethod": "HR_RESERVE", "maxHeartRateUsed": max_hr, "restingHeartRateUsed": rest_hr,
                                        "zone1Floor": 120, "zone2Floor": 135, "zone3Floor": 152, "zone4Floor": 167, "zone5Floor": 180}])
    db.put_snapshot(conn, "fitness_age", {"chronologicalAge": 33, "fitnessAge": 24.6, "achievableFitnessAge": 22})
    db.put_snapshot(conn, "endurance_latest", {"overallScore": 7240, "classificationLowerLimitIntermediate": 5100,
                                               "classificationLowerLimitTrained": 6200, "classificationLowerLimitWellTrained": 7000,
                                               "classificationLowerLimitExpert": 8200, "classificationLowerLimitSuperior": 9200,
                                               "classificationLowerLimitElite": 10200})
    db.put_snapshot(conn, "endurance_range", {"groupMap": {
        (today - timedelta(weeks=52 - w, days=today.weekday())).isoformat(): {"groupAverage": 5600 + 32 * w + rng.randint(-80, 80)} for w in range(52)}})
    db.put_snapshot(conn, "weekly_stress", [{"calendarDate": (today - timedelta(weeks=26 - w)).isoformat(), "value": rng.randint(24, 38)} for w in range(26)])
    pr = [(3, 5000), (4, 10000), (5, 21097.5)]
    db.put_snapshot(conn, "personal_records", [
        {"typeId": tid, "value": round(time_for_vdot(v_now - 0.3, dd)), "activityName": "Race", "prStartTimeGmtFormatted": (today - timedelta(days=40)).isoformat()}
        for tid, dd in pr] + [{"typeId": 7, "value": 34000, "activityName": "Long run", "prStartTimeGmtFormatted": (today - timedelta(days=13)).isoformat()}])
    conn.execute("INSERT INTO sync_log (started_at, finished_at, status, detail) VALUES (?,?,?,?)",
                 (db.now_iso(), db.now_iso(), "ok", "demo data"))
    conn.commit()
    conn.close()


def _other(day: date, type_key: str, rng, aid: int, rest_hr: int, max_hr: int) -> dict:
    bike = type_key == "road_biking"
    dur = rng.uniform(3600, 5400) if bike else rng.uniform(2400, 3300)
    hr = rng.uniform(128, 142) if bike else rng.uniform(105, 120)
    return {"activityId": aid, "activityName": "Road ride" if bike else "Strength", "activityType": {"typeKey": type_key},
            "startTimeLocal": f"{day.isoformat()} 18:00:00", "distance": round(dur / 3600 * rng.uniform(26, 30) * 1000) if bike else 0,
            "duration": dur, "movingDuration": dur, "elevationGain": round(dur / 3600 * 250) if bike else 0, "averageHR": round(hr),
            "maxHR": round(hr + 25), "averageSpeed": rng.uniform(7.2, 8.3) if bike else 0, "calories": dur / 60 * 9,
            "activityTrainingLoad": round(dur / 60 * 0.9), "aerobicTrainingEffect": 2.4 if bike else 1.2, "anaerobicTrainingEffect": 0.3}


if __name__ == "__main__":
    build()
    print("Demo database written.")
