"""Paths and user settings for the Garmin dashboard."""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("GARMIN_DASH_DATA", ROOT / "data"))
DB_PATH = Path(os.getenv("GARMIN_DASH_DB", DATA_DIR / "garmin.sqlite"))
DEMO_DB_PATH = DATA_DIR / "demo.sqlite"
TOKEN_DIR = Path(os.getenv("GARMINTOKENS", Path.home() / ".garminconnect"))
SETTINGS_PATH = ROOT / "settings.json"
DASHBOARD_PORT = int(os.getenv("GARMIN_DASH_PORT", "8765"))

DEFAULT_SETTINGS = {
    # Used when Garmin has no value (e.g. no resting HR yet).
    "max_hr": None,
    "resting_hr": None,
    "sex": "male",  # "male" | "female" — only used for the Banister TRIMP factor
    # Optional goal race shown on the dashboard. Leave name empty to hide it.
    "goal_race": {"name": "", "date": "", "distance_km": 42.195, "target_time": ""},
    "sleep_goal_h": 8,
    # How far back the first full sync goes.
    "history_days": 730,
}


def load_settings() -> dict:
    settings = json.loads(json.dumps(DEFAULT_SETTINGS))
    if SETTINGS_PATH.exists():
        user = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        for key, value in user.items():
            if isinstance(value, dict) and isinstance(settings.get(key), dict):
                settings[key].update(value)
            else:
                settings[key] = value
    return settings
