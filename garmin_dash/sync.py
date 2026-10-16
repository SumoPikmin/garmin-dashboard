"""Pull Garmin Connect data into the local SQLite DB. Incremental after the first run."""
from __future__ import annotations

import time
from datetime import date, timedelta
from typing import Any, Callable, Iterator

from . import db
from .client import get_client
from .config import load_settings

PAUSE_S = 0.25  # be gentle with Garmin's API


def _chunks(start: date, end: date, size: int) -> Iterator[tuple[str, str]]:
    cur = start
    while cur <= end:
        stop = min(cur + timedelta(days=size - 1), end)
        yield cur.isoformat(), stop.isoformat()
        cur = stop + timedelta(days=1)
        time.sleep(PAUSE_S)


def _days(start: date, end: date) -> Iterator[str]:
    cur = start
    while cur <= end:
        yield cur.isoformat()
        cur += timedelta(days=1)
        time.sleep(PAUSE_S)


def _start_for(conn, kind: str, window_days: int, today: date, full: bool) -> date:
    """Re-fetch the last 3 days (Garmin finalises them late), otherwise only what's missing."""
    earliest = today - timedelta(days=window_days)
    if full:
        return earliest
    row = conn.execute("SELECT MAX(date) FROM daily WHERE kind = ?", (kind,)).fetchone()
    if not row or not row[0]:
        return earliest
    return max(earliest, date.fromisoformat(row[0]) - timedelta(days=3))


def _bb_summary(item: dict) -> dict:
    levels = []
    for point in item.get("bodyBatteryValuesArray") or []:
        nums = [x for x in point if isinstance(x, (int, float)) and 0 <= x <= 100]
        if nums:
            levels.append(nums[-1])
    return {
        "charged": item.get("charged"),
        "drained": item.get("drained"),
        "max": max(levels) if levels else None,
        "min": min(levels) if levels else None,
        "end": levels[-1] if levels else None,
    }


def sync(full: bool = False, history_days: int | None = None, log: Callable[[str], Any] = print) -> dict:
    settings = load_settings()
    history = history_days or settings["history_days"]
    client = get_client()
    conn = db.connect()
    today = date.today()
    started = db.now_iso()
    report: dict[str, str] = {}

    def step(name: str, fn: Callable[[], int | None]) -> None:
        log(f"- {name} ...")
        try:
            n = fn()
            conn.commit()
            report[name] = f"ok ({n})" if n is not None else "ok"
        except Exception as e:  # keep going: one endpoint failing shouldn't kill the sync
            conn.rollback()
            report[name] = f"error: {type(e).__name__}: {e}"
        log(f"  {name}: {report[name]}")

    # ---------- activities ----------
    def activities() -> int:
        row = conn.execute("SELECT MAX(date) FROM activities").fetchone()
        start = today - timedelta(days=history)
        if row and row[0] and not full:
            start = max(start, date.fromisoformat(row[0]) - timedelta(days=3))
        acts = client.get_activities_by_date(start.isoformat(), today.isoformat())
        return db.upsert_activities(conn, acts or [])

    # ---------- ranged daily series ----------
    def ranged(kind: str, window: int, chunk: int, fetch: Callable[[str, str], list[dict]],
               date_of: Callable[[dict], str | None], shape: Callable[[dict], Any] = lambda x: x) -> Callable[[], int]:
        def run() -> int:
            n = 0
            for s, e in _chunks(_start_for(conn, kind, window, today, full), today, chunk):
                items = fetch(s, e) or []
                for item in [items] if isinstance(items, dict) else items:
                    d = date_of(item)
                    if d:
                        db.put_daily(conn, d[:10], kind, shape(item))
                        n += 1
            return n
        return run

    def per_day(kind: str, window: int, fetch: Callable[[str], Any]) -> Callable[[], int]:
        def run() -> int:
            n = 0
            for d in _days(_start_for(conn, kind, window, today, full), today):
                data = fetch(d)
                if data:
                    db.put_daily(conn, d, kind, data)
                    n += 1
            return n
        return run

    step("activities", activities)
    step("vo2max", ranged(
        "vo2max", history, 28, client.get_max_metrics_range,
        lambda x: (x.get("generic") or {}).get("calendarDate") or (x.get("cycling") or {}).get("calendarDate") or x.get("calendarDate"),
    ))
    step("race_predictions", ranged(
        "race_pred", min(history, 365), 180,
        lambda s, e: client.get_race_predictions(s, e, "daily"),
        lambda x: x.get("calendarDate"),
    ))
    step("hrv", ranged(
        "hrv", history, 28, lambda s, e: (client.get_hrv_data_range(s, e) or {}).get("hrvSummaries", []),
        lambda x: x.get("calendarDate"),
    ))
    step("sleep", ranged("sleep", history, 112, client.get_sleep_daily, lambda x: x.get("calendarDate")))
    step("resting_hr", ranged("rhr", history, 360, client.get_rhr_daily, lambda x: x.get("calendarDate")))
    step("steps", ranged("steps", history, 112, client.get_daily_steps, lambda x: x.get("calendarDate")))
    step("body_battery", ranged(
        "body_battery", min(history, 180), 28, client.get_body_battery, lambda x: x.get("date"), _bb_summary,
    ))
    step("weight", ranged(
        "weight", history, 90, lambda s, e: (client.get_body_composition(s, e) or {}).get("dateWeightList", []),
        lambda x: x.get("calendarDate"),
    ))
    step("hill_score", ranged(
        "hill", min(history, 365), 28, lambda s, e: (client.get_hill_score(s, e) or {}).get("hillScoreDTOList", []),
        lambda x: x.get("calendarDate"),
    ))

    # ---------- per-day endpoints (no range API) — shorter windows ----------
    step("training_status", per_day("training_status", 42, client.get_training_status))
    step("training_readiness", per_day("readiness", 90, client.get_training_readiness))
    step("daily_stats", per_day("stats", 30, client.get_stats))

    # ---------- snapshots ----------
    t = today.isoformat()
    snapshots: dict[str, Callable[[], Any]] = {
        "profile": client.get_user_profile,
        "full_name": client.get_full_name,
        "personal_records": client.get_personal_record,
        "lactate_threshold": client.get_lactate_threshold,
        "hr_zones": client.get_heart_rate_zones,
        "fitness_age": lambda: client.get_fitnessage_data(t),
        "race_pred_latest": client.get_race_predictions,
        "endurance_latest": lambda: client.get_endurance_score(t),
        "endurance_range": lambda: client.get_endurance_score((today - timedelta(days=364)).isoformat(), t),
        "weekly_stress": lambda: client.get_weekly_stress(t, 52),
        "weekly_intensity": lambda: client.get_weekly_intensity_minutes((today - timedelta(days=364)).isoformat(), t),
        "devices": client.get_devices,
    }
    for kind, fetch in snapshots.items():
        def snap(kind=kind, fetch=fetch) -> None:
            db.put_snapshot(conn, kind, fetch())
            time.sleep(PAUSE_S)
        step(kind, snap)

    errors = [k for k, v in report.items() if v.startswith("error")]
    conn.execute(
        "INSERT INTO sync_log (started_at, finished_at, status, detail) VALUES (?,?,?,?)",
        (started, db.now_iso(), "ok" if len(errors) < len(report) else "failed", repr(report)),
    )
    conn.commit()
    conn.close()
    return {"errors": errors, "steps": report}
