"""Garmin MCP server: lets Claude sync and query your Garmin data, and opens the dashboard.

Registered (user scope) in %APPDATA%/Claude/claude_desktop_config.json and ~/.claude.json as "garmin".
Add `--demo` at the end to serve the synthetic demo data instead.
"""
from __future__ import annotations

import socket
import sys
import threading
from datetime import date, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mcp.server import MCPServer  # noqa: E402
from mcp.server.mcpserver.exceptions import ToolError  # noqa: E402

from garmin_dash import analytics, db  # noqa: E402
from garmin_dash.config import DASHBOARD_PORT  # noqa: E402

DEMO = "--demo" in sys.argv
if DEMO:
    db.use_demo()

mcp = MCPServer(
    "garmin",
    instructions=(
        "Personal Garmin Connect data stored locally. Call sync_garmin first if data looks stale "
        "(see last_sync in get_overview). Times are seconds unless a *_fmt field is given. "
        "Use run_sql for anything the dedicated tools don't cover."
    ),
)


def _data() -> analytics.Data:
    return analytics.Data(_conn())


def _conn():
    from garmin_dash import config
    if not config.DB_PATH.exists():
        raise ToolError("No Garmin data yet: run `python login.py` once, then `python sync.py` (or call sync_garmin).")
    return db.connect(readonly=True)


def _trim(series: list[dict], days: int, key: str = "date") -> list[dict]:
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    return [p for p in series if (p.get(key) or "") >= cutoff]


@mcp.tool()
def sync_garmin(full: bool = False) -> dict:
    """Download new data from Garmin Connect into the local database (incremental; full=True re-pulls the history window).
    The very first sync takes several minutes — prefer running `python sync.py` in a terminal for that."""
    if DEMO:
        return {"skipped": "server is running in --demo mode"}
    from garmin_dash.client import NotLoggedIn
    from garmin_dash.sync import sync
    try:
        return sync(full=full, log=lambda _msg: None)
    except NotLoggedIn as e:
        raise ToolError(str(e)) from e


@mcp.tool()
def get_overview() -> dict:
    """Headline numbers: VO2max (Garmin + own estimates), race predictions, fitness/fatigue/form, ACWR,
    recovery score, Garmin training status & readiness, this week's volume, and auto-generated insights."""
    full = analytics.build_dashboard(_conn())
    v, l, r, g, vol = full["vo2max"], full["load"], full["recovery"], full["garmin_status"], full["volume"]
    return {
        "meta": full["meta"],
        "vo2max": {k: v[k] for k in ("garmin_current", "garmin_change_90d", "hr_estimate_current", "vdot_current",
                                     "uth_estimate", "category", "fitness_age", "chronological_age")},
        "race_predictions": [{k: row.get(k) for k in ("race", "garmin_fmt", "vdot_fmt", "riegel_fmt", "tanda_fmt",
                                                       "consensus_fmt", "pace_fmt")} for row in full["predictions"]["rows"]],
        "prediction_basis": full["predictions"]["basis"],
        "training_load": {k: l.get(k) for k in ("ctl", "atl", "tsb", "form_label", "acwr", "acwr_zone", "ramp_7d", "monotony", "strain")},
        "garmin_status": {k: g.get(k) for k in ("status", "acute_load", "chronic_load", "acwr", "readiness_current",
                                                "endurance_score", "endurance_class", "hill_score", "lactate_threshold")},
        "recovery": {k: r.get(k) for k in ("score", "label", "components", "hrv_last", "hrv_status", "rhr_7d",
                                           "rhr_delta", "sleep_7d_h", "sleep_7d_score", "sleep_debt_7d_h")},
        "volume": {k: vol.get(k) for k in ("last_7d", "last_28d", "ytd", "last_year_ytd", "run_km_4w_avg", "streak_days")},
        "intensity_28d": {k: full["intensity"].get(k) for k in ("pct", "easy_pct", "moderate_pct", "hard_pct", "verdict")},
        "goal_race": full["goal"],
        "insights": [i["text"] for i in full["insights"]],
    }


@mcp.tool()
def get_activities(days: int = 30, sport: str | None = None, limit: int = 50) -> list[dict]:
    """Recent activities (newest first). sport: run | bike | swim | strength | walk | other."""
    d = _data()
    acts = analytics.activities_list(d, limit=10_000, sport=sport)
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    return [a for a in acts if a["date"] >= cutoff][:limit]


@mcp.tool()
def get_activity_details(activity_id: int) -> dict:
    """Live from Garmin: full activity summary, lap splits and HR time-in-zones for one activity."""
    from garmin_dash.client import NotLoggedIn, get_client
    try:
        c = get_client()
    except NotLoggedIn as e:
        raise ToolError(str(e)) from e
    out: dict[str, Any] = {}
    for key, fn in (("summary", c.get_activity), ("splits", c.get_activity_splits), ("hr_zones", c.get_activity_hr_in_timezones)):
        try:
            out[key] = fn(str(activity_id))
        except Exception as e:
            out[key] = f"error: {e}"
    if isinstance(out.get("splits"), dict):  # keep it readable
        laps = out["splits"].get("lapDTOs") or []
        out["splits"] = [{"lap": i + 1, "km": round((l.get("distance") or 0) / 1000, 2),
                          "time": analytics.fmt_time(l.get("duration")),
                          "pace": analytics.fmt_pace((l.get("duration") or 0) / ((l.get("distance") or 1) / 1000)),
                          "avg_hr": l.get("averageHR"), "elev_gain": l.get("elevationGain")} for i, l in enumerate(laps)]
    return out


@mcp.tool()
def get_vo2max(days: int = 365) -> dict:
    """VO2max history: Garmin's value, an HR-based estimate from your runs (%HRR method), rolling-best VDOT, Uth estimate."""
    v = analytics.vo2max_section(_data())
    for k in ("garmin_series", "cycling_series", "hr_estimate_series", "vdot_series"):
        v[k] = _trim(v[k], days)
    return v


@mcp.tool()
def get_race_predictions(history_days: int = 180) -> dict:
    """5K/10K/Half/Marathon predictions from Garmin, VDOT (Daniels), Riegel and Tanda (volume-based marathon),
    the effort they're based on, the 8-week training volume, and Garmin's prediction history (weekly samples)."""
    p = analytics.predictions_section(_data())
    p["garmin_series"] = {race: [{**x, "time_fmt": analytics.fmt_time(x["value"])} for x in _trim(s, history_days)][::-7][::-1]
                          for race, s in p["garmin_series"].items()}
    return p


@mcp.tool()
def get_training_load(days: int = 90) -> dict:
    """Daily TRIMP load with fitness (CTL, 42d), fatigue (ATL, 7d), form (TSB), ACWR, monotony and strain,
    plus Garmin's own training status, acute/chronic load and load focus."""
    d = _data()
    l = analytics.load_section(d)
    l["series"] = _trim(l["series"], days)
    g = analytics.garmin_status_section(d)
    g["readiness_series"] = _trim(g["readiness_series"], days)
    return {"computed": l, "garmin": g}


@mcp.tool()
def get_recovery(days: int = 30) -> dict:
    """HRV, resting HR, sleep, body battery, steps, stress and weight series plus the computed recovery score."""
    r = analytics.recovery_section(_data())
    for k, v in list(r.items()):
        if isinstance(v, list):
            r[k] = _trim(v, days)
    return r


@mcp.tool()
def get_weekly_volume(weeks: int = 12) -> dict:
    """Weekly distance/hours per sport, long run per week, YTD vs last year, and the 28-day intensity distribution."""
    d = _data()
    v = analytics.volume_section(d)
    v.pop("heatmap")
    v["weekly"] = v["weekly"][-weeks:]
    v["intensity_28d"] = analytics.intensity_section(d)
    v["efficiency"] = {k: x for k, x in analytics.efficiency_section(d).items() if not isinstance(x, list)}
    return v


@mcp.tool()
def get_personal_records() -> dict:
    """Garmin personal records plus best efforts computed from your activities (all-time and this year)."""
    return analytics.records_section(_data())


@mcp.tool()
def run_sql(query: str) -> list[dict]:
    """Read-only SQL on the local DB. Tables: activities(activity_id, start_local, date, type, name, distance_m,
    duration_s, moving_s, elev_gain_m, avg_hr, max_hr, avg_speed, calories, training_load, aerobic_te, anaerobic_te,
    vo2max, avg_cadence, data JSON), daily(date, kind, data JSON) with kind in stats|sleep|hrv|rhr|steps|readiness|
    training_status|vo2max|race_pred|body_battery|weight|hill, snapshots(kind, data JSON, fetched_at), sync_log.
    Use json_extract(data, '$.field') for raw Garmin fields. Max 500 rows."""
    import sqlite3
    try:
        rows = _conn().execute(query).fetchmany(500)
    except sqlite3.Error as e:  # let Claude see the SQL error and fix the query
        raise ToolError(f"SQL error: {e}") from e
    return [dict(r) for r in rows]


_server_thread: threading.Thread | None = None


@mcp.tool()
def open_dashboard() -> str:
    """Start the local web dashboard (if not running) and return its URL."""
    global _server_thread
    url = f"http://127.0.0.1:{DASHBOARD_PORT}/"
    with socket.socket() as s:
        if s.connect_ex(("127.0.0.1", DASHBOARD_PORT)) == 0:
            return f"Dashboard already running: {url}"
    import uvicorn
    from dashboard import create_app
    config = uvicorn.Config(create_app(demo=DEMO), host="127.0.0.1", port=DASHBOARD_PORT, log_level="warning")
    _server_thread = threading.Thread(target=uvicorn.Server(config).run, daemon=True)
    _server_thread.start()
    return f"Dashboard started: {url}"


if __name__ == "__main__":
    mcp.run()
