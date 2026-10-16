"""SQLite storage: raw Garmin JSON plus a few extracted columns for fast queries."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Iterable

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS activities (
    activity_id   INTEGER PRIMARY KEY,
    start_local   TEXT NOT NULL,          -- 'YYYY-MM-DD HH:MM:SS'
    date          TEXT NOT NULL,          -- 'YYYY-MM-DD'
    type          TEXT,                   -- typeKey, e.g. running, cycling
    name          TEXT,
    distance_m    REAL,
    duration_s    REAL,
    moving_s      REAL,
    elev_gain_m   REAL,
    avg_hr        REAL,
    max_hr        REAL,
    avg_speed     REAL,                   -- m/s
    calories      REAL,
    training_load REAL,                   -- Garmin activityTrainingLoad
    aerobic_te    REAL,
    anaerobic_te  REAL,
    vo2max        REAL,
    avg_cadence   REAL,
    data          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_act_date ON activities(date);
CREATE INDEX IF NOT EXISTS idx_act_type ON activities(type);

-- One row per (day, kind): kind = stats | sleep | hrv | readiness | training_status
--                                  | vo2max | race_pred | endurance | hill | body_battery | weight
CREATE TABLE IF NOT EXISTS daily (
    date  TEXT NOT NULL,
    kind  TEXT NOT NULL,
    data  TEXT NOT NULL,
    PRIMARY KEY (date, kind)
);

-- Latest value of things that are not day-based (profile, PRs, HR zones, LT ...)
CREATE TABLE IF NOT EXISTS snapshots (
    kind        TEXT PRIMARY KEY,
    data        TEXT NOT NULL,
    fetched_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT,
    finished_at TEXT,
    status      TEXT,
    detail      TEXT
);
"""


def use_demo() -> None:
    """Point everything at the synthetic demo database instead of your real data."""
    config.DB_PATH = config.DEMO_DB_PATH


def connect(readonly: bool = False) -> sqlite3.Connection:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    if readonly:
        conn = sqlite3.connect(f"file:{config.DB_PATH.as_posix()}?mode=ro", uri=True, check_same_thread=False)
    else:
        conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
        conn.executescript(SCHEMA)
    conn.row_factory = sqlite3.Row
    return conn


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _num(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def upsert_activities(conn: sqlite3.Connection, activities: Iterable[dict]) -> int:
    n = 0
    for a in activities:
        aid = a.get("activityId")
        start = a.get("startTimeLocal")
        if not aid or not start:
            continue
        type_key = (a.get("activityType") or {}).get("typeKey")
        cadence = a.get("averageRunningCadenceInStepsPerMinute") or a.get("averageBikingCadenceInRevPerMinute")
        conn.execute(
            """INSERT OR REPLACE INTO activities VALUES
               (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                aid, start, start[:10], type_key, a.get("activityName"),
                _num(a.get("distance")), _num(a.get("duration")), _num(a.get("movingDuration")),
                _num(a.get("elevationGain")), _num(a.get("averageHR")), _num(a.get("maxHR")),
                _num(a.get("averageSpeed")), _num(a.get("calories")),
                _num(a.get("activityTrainingLoad")), _num(a.get("aerobicTrainingEffect")),
                _num(a.get("anaerobicTrainingEffect")), _num(a.get("vO2MaxValue")),
                _num(cadence), json.dumps(a),
            ),
        )
        n += 1
    conn.commit()
    return n


def put_daily(conn: sqlite3.Connection, date: str, kind: str, data: Any) -> None:
    if data in (None, {}, []):
        return
    conn.execute("INSERT OR REPLACE INTO daily VALUES (?,?,?)", (date, kind, json.dumps(data)))


def put_snapshot(conn: sqlite3.Connection, kind: str, data: Any) -> None:
    if data in (None, {}, []):
        return
    conn.execute("INSERT OR REPLACE INTO snapshots VALUES (?,?,?)", (kind, json.dumps(data), now_iso()))


def get_daily(conn: sqlite3.Connection, kind: str, start: str | None = None) -> list[tuple[str, Any]]:
    sql = "SELECT date, data FROM daily WHERE kind = ?"
    args: list[Any] = [kind]
    if start:
        sql += " AND date >= ?"
        args.append(start)
    sql += " ORDER BY date"
    return [(r["date"], json.loads(r["data"])) for r in conn.execute(sql, args)]


def get_snapshot(conn: sqlite3.Connection, kind: str) -> Any:
    row = conn.execute("SELECT data FROM snapshots WHERE kind = ?", (kind,)).fetchone()
    return json.loads(row["data"]) if row else None


def existing_daily_dates(conn: sqlite3.Connection, kind: str) -> set[str]:
    return {r[0] for r in conn.execute("SELECT date FROM daily WHERE kind = ?", (kind,))}


def last_sync(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute(
        "SELECT * FROM sync_log WHERE status = 'ok' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return dict(row) if row else None
